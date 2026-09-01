-- Migration: 000014_avm_deal_outcomes.sql
-- AVM Deal Outcome Recovery & Valuation Calibration (ODP-AVM-DEAL-OUTCOME-001)
--
-- ODP-FR-AVM-005 / 008: Transaction outcome collection.
-- Captures realized deal outcomes, settlement prices, reasons for no-deal,
-- deal terms (JSONB), and maps them to authoritative valuation baselines via valuation_id.
-- settlement_price is sensitive financial data protected by finance:view.

CREATE SCHEMA IF NOT EXISTS avm;

CREATE TABLE IF NOT EXISTS avm.deal_outcomes (
    outcome_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    valuation_id VARCHAR(100) NOT NULL,
    store_id VARCHAR(100) NOT NULL,
    sold BOOLEAN NOT NULL,
    settlement_price NUMERIC(16, 2) CHECK (settlement_price IS NULL OR settlement_price >= 0),
    settlement_date DATE,
    duration_days NUMERIC(10, 2) NOT NULL DEFAULT 0.0 CHECK (duration_days >= 0),
    no_deal_reason_code VARCHAR(50) CHECK (
        no_deal_reason_code IS NULL
        OR no_deal_reason_code IN (
            'PRICE_GAP',
            'CONDITION',
            'FINANCING',
            'WITHDRAWN_BY_OWNER',
            'OTHER'
        )
    ),
    deal_terms JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(deal_terms) = 'object'),
    source_authority VARCHAR(100) NOT NULL DEFAULT 'official_dealroom',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_deal_outcome_valuation_required CHECK (valuation_id <> ''),
    CONSTRAINT chk_deal_outcome_sold_price CHECK (
        (sold = TRUE AND settlement_price IS NOT NULL AND settlement_price > 0 AND no_deal_reason_code IS NULL)
        OR
        (sold = FALSE AND (settlement_price IS NULL OR settlement_price = 0) AND no_deal_reason_code IS NOT NULL)
    )
);

COMMENT ON TABLE avm.deal_outcomes IS
    'Authoritative transaction outcome records for AVM calibration and liquidity training. settlement_price is sensitive financial data requiring finance:view clearance.';

COMMENT ON COLUMN avm.deal_outcomes.valuation_id IS
    'Mandatory reference to the corresponding AVM valuation report/case baseline (Fair/Reserve/Asking prices).';

COMMENT ON COLUMN avm.deal_outcomes.settlement_price IS
    'Sensitive financial settlement price in TWD (protected under finance:view and ODP-BR-OPS-002 export audit).';

COMMENT ON COLUMN avm.deal_outcomes.no_deal_reason_code IS
    'Reason why deal was not completed: PRICE_GAP, CONDITION, FINANCING, WITHDRAWN_BY_OWNER, OTHER.';

CREATE INDEX IF NOT EXISTS idx_deal_outcomes_valuation_id
    ON avm.deal_outcomes(valuation_id);

CREATE INDEX IF NOT EXISTS idx_deal_outcomes_store_id
    ON avm.deal_outcomes(store_id);

CREATE INDEX IF NOT EXISTS idx_deal_outcomes_settlement_date
    ON avm.deal_outcomes(settlement_date DESC);

-- Register schema migration in odp_runtime if available
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'odp_runtime' AND table_name = 'schema_migrations') THEN
        INSERT INTO odp_runtime.schema_migrations (migration_id, applied_at)
        VALUES ('000014_avm_deal_outcomes', CURRENT_TIMESTAMP)
        ON CONFLICT (migration_id) DO NOTHING;
    END IF;
END $$;
