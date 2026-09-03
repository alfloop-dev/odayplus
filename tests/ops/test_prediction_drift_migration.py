from pathlib import Path


MIGRATION = Path("infra/db/migrations/000017_learninghub_prediction_drift.sql")


def test_prediction_drift_receipt_requires_replayable_lineage() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    for fragment in (
        "CREATE TABLE IF NOT EXISTS learning.prediction_drift_evaluations",
        "reference_snapshot_id       VARCHAR(255) NOT NULL",
        "current_snapshot_id         VARCHAR(255) NOT NULL",
        "model_version               VARCHAR(100) NOT NULL",
        "cohort_key                  VARCHAR(255) NOT NULL",
        "prediction_output_types     JSONB NOT NULL",
        "decision_policy_version_id  VARCHAR(100) NOT NULL",
        "CONSTRAINT chk_prediction_drift_snapshot_pair",
        "reference_snapshot_id <> current_snapshot_id",
    ):
        assert fragment in sql


def test_prediction_drift_receipt_is_indexed_by_model_cohort_and_snapshots() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "idx_prediction_drift_model_cohort" in sql
    assert "model_name, model_version, cohort_key, created_at" in sql
    assert "idx_prediction_drift_snapshots" in sql
    assert "reference_snapshot_id, current_snapshot_id" in sql
