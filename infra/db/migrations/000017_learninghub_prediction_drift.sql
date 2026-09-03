-- ODP-FR-LH-005 / ODP-LH-PREDICTION-DRIFT-001
--
-- Prediction drift is a comparison of two immutable population snapshots. A
-- report without the two snapshot identities, model boundary, cohort, and
-- policy version cannot be replayed or audited, so those fields are required
-- here instead of being embedded only in an opaque report blob.

CREATE SCHEMA IF NOT EXISTS learning;

CREATE TABLE IF NOT EXISTS learning.prediction_drift_evaluations (
    evaluation_id               VARCHAR(100) PRIMARY KEY,
    model_name                  VARCHAR(255) NOT NULL,
    model_version               VARCHAR(100) NOT NULL,
    reference_snapshot_id       VARCHAR(255) NOT NULL,
    current_snapshot_id         VARCHAR(255) NOT NULL,
    cohort_key                  VARCHAR(255) NOT NULL,
    prediction_columns          TEXT[] NOT NULL,
    prediction_output_types     JSONB NOT NULL,
    drift_detected              BOOLEAN NOT NULL,
    drifted_columns             TEXT[] NOT NULL DEFAULT '{}',
    drift_share                 NUMERIC(8, 6) NOT NULL,
    decision_policy_version_id  VARCHAR(100) NOT NULL,
    report_json                 JSONB NOT NULL,
    alert_id                    VARCHAR(100),
    created_at                  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_prediction_drift_snapshot_pair CHECK (
        reference_snapshot_id <> current_snapshot_id
    ),
    CONSTRAINT chk_prediction_drift_cohort CHECK (cohort_key <> ''),
    CONSTRAINT chk_prediction_drift_columns CHECK (cardinality(prediction_columns) > 0),
    CONSTRAINT chk_prediction_drift_output_types CHECK (
        jsonb_typeof(prediction_output_types) = 'object'
    ),
    CONSTRAINT chk_prediction_drift_share CHECK (drift_share >= 0 AND drift_share <= 1),
    CONSTRAINT chk_prediction_drift_report CHECK (jsonb_typeof(report_json) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_prediction_drift_model_cohort
    ON learning.prediction_drift_evaluations(
        model_name, model_version, cohort_key, created_at
    );
CREATE INDEX IF NOT EXISTS idx_prediction_drift_snapshots
    ON learning.prediction_drift_evaluations(
        reference_snapshot_id, current_snapshot_id
    );
CREATE INDEX IF NOT EXISTS idx_prediction_drift_policy
    ON learning.prediction_drift_evaluations(decision_policy_version_id);
