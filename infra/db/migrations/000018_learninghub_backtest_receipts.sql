-- ODP-FR-LH-003 / ODP-LH003-BACKTEST-RELEASE-GATE-001
--
-- LearningHub Backtest release admission gate receipts.
-- Backtest receipts bind model version, dataset snapshot, code version (git SHA),
-- and DecisionPolicy version to enforce versioned release gating.

CREATE SCHEMA IF NOT EXISTS learning;

CREATE TABLE IF NOT EXISTS learning.backtest_receipts (
    receipt_id                  VARCHAR(100) PRIMARY KEY,
    model_name                  VARCHAR(255) NOT NULL,
    model_version               VARCHAR(100) NOT NULL,
    dataset_snapshot_id         VARCHAR(255) NOT NULL,
    code_version                VARCHAR(100) NOT NULL,
    decision_policy_version_id  VARCHAR(100) NOT NULL,
    status                      VARCHAR(50) NOT NULL,
    metrics                     JSONB NOT NULL DEFAULT '{}'::jsonb,
    baseline_metrics            JSONB NOT NULL DEFAULT '{}'::jsonb,
    thresholds                  JSONB NOT NULL DEFAULT '[]'::jsonb,
    failed_rules                JSONB NOT NULL DEFAULT '[]'::jsonb,
    horizon_metrics             JSONB NOT NULL DEFAULT '{}'::jsonb,
    calibration_summary         JSONB NOT NULL DEFAULT '{}'::jsonb,
    requested_by                VARCHAR(100) NOT NULL DEFAULT 'system',
    audit_event_id              VARCHAR(100),
    report_artifact_uri         VARCHAR(1000),
    report_sha256               VARCHAR(100),
    created_at                  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_backtest_receipt_model_version CHECK (model_version <> ''),
    CONSTRAINT chk_backtest_receipt_snapshot CHECK (dataset_snapshot_id <> ''),
    CONSTRAINT chk_backtest_receipt_code_version CHECK (code_version <> ''),
    CONSTRAINT chk_backtest_receipt_policy CHECK (decision_policy_version_id <> ''),
    CONSTRAINT chk_backtest_receipt_status CHECK (status IN ('PASSED', 'WARNING', 'FAILED'))
);

CREATE INDEX IF NOT EXISTS idx_backtest_receipts_model_version
    ON learning.backtest_receipts(model_name, model_version, created_at);
CREATE INDEX IF NOT EXISTS idx_backtest_receipts_snapshot
    ON learning.backtest_receipts(dataset_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_backtest_receipts_policy
    ON learning.backtest_receipts(decision_policy_version_id);
