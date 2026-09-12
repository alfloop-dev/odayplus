-- ODP-FR-FCT-008: ForecastOps feedback mechanism (ODP-FORECAST-FEEDBACK-001)
--
-- Adds forecastops.feedback table for three feedback types:
-- CONTEXT_ANNOTATION, OUTCOME_CORRECTION, ALERT_DISPOSITION.
-- Enforces ODP-BR-GOV-001: feedback never directly overrides predictions or decisions.

CREATE SCHEMA IF NOT EXISTS forecastops;

CREATE TABLE IF NOT EXISTS forecastops.feedback (
    feedback_id VARCHAR(100) PRIMARY KEY,
    tenant_id VARCHAR(100) NOT NULL,
    store_id VARCHAR(100) NOT NULL,
    feedback_type VARCHAR(50) NOT NULL,
    target_date_start DATE NOT NULL,
    target_date_end DATE NOT NULL,
    reason TEXT NOT NULL,
    status VARCHAR(50) NOT NULL,
    corrected_revenue DOUBLE PRECISION,
    alert_id VARCHAR(100),
    disposition VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(100) NOT NULL,
    approved_at TIMESTAMP WITH TIME ZONE,
    approved_by VARCHAR(100),
    rejection_reason TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_forecastops_feedback_tenant_store
    ON forecastops.feedback (tenant_id, store_id);

CREATE INDEX IF NOT EXISTS idx_forecastops_feedback_type_status
    ON forecastops.feedback (feedback_type, status);
