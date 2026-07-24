-- ODay Plus model-ready contracts, version 2026-07-24.1.
--
-- Apply only after the canonical PostgreSQL and data-plane migrations/backfill.
-- This artifact creates training rows exclusively by selecting persisted source
-- records. It does not create date spines, labels, or fallback rows.

CREATE SCHEMA IF NOT EXISTS model_ready;

CREATE TABLE IF NOT EXISTS model_ready.view_contracts (
    relation_name TEXT PRIMARY KEY,
    view_name TEXT NOT NULL UNIQUE,
    view_version TEXT NOT NULL,
    source_relations TEXT[] NOT NULL,
    contract_state TEXT NOT NULL CHECK (contract_state IN ('ACTIVE', 'BLOCKED')),
    training_enabled BOOLEAN NOT NULL,
    blocked_reason TEXT,
    installer_sha256 TEXT CHECK (
        installer_sha256 IS NULL OR installer_sha256 ~ '^[0-9a-f]{64}$'
    ),
    installed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (contract_state = 'ACTIVE' AND training_enabled AND blocked_reason IS NULL)
        OR
        (contract_state = 'BLOCKED' AND NOT training_enabled AND blocked_reason IS NOT NULL)
    ),
    CHECK (NOT training_enabled OR cardinality(source_relations) > 0)
);

COMMENT ON TABLE model_ready.view_contracts IS
    'Fail-closed registry for versioned model-ready relations and outcome readiness.';

CREATE OR REPLACE VIEW model_ready.forecast_training_view AS
WITH transaction_source AS (
    SELECT
        store.tenant_id,
        txn.transaction_id,
        txn.store_id,
        txn.event_time,
        txn.observation_time,
        txn.ingested_at,
        txn.net_amount,
        source.source_snapshot_ids,
        source.source_run_complete,
        source.source_run_finished_at
    FROM core.transactions AS txn
    INNER JOIN core.stores AS store
        ON store.store_id = txn.store_id
    LEFT JOIN LATERAL (
        SELECT
            array_agg(
                DISTINCT lineage.source_snapshot_id::text
                ORDER BY lineage.source_snapshot_id::text
            ) AS source_snapshot_ids,
            bool_and(
                ingestion.status = 'SUCCEEDED'
                AND ingestion.finished_at IS NOT NULL
            ) AS source_run_complete,
            max(ingestion.finished_at) AS source_run_finished_at
        FROM data_plane.canonical_lineage AS lineage
        INNER JOIN data_plane.ingestion_runs AS ingestion
            ON ingestion.run_id = lineage.run_id
        WHERE lineage.tenant_id = store.tenant_id
          AND lineage.canonical_table = 'core.transactions'
          AND lineage.canonical_id = txn.transaction_id
    ) AS source ON TRUE
    WHERE txn.transaction_status = 'succeeded'
      AND txn.currency = 'TWD'
),
daily_source_ids AS (
    SELECT
        transaction_source.tenant_id,
        transaction_source.store_id,
        (transaction_source.event_time AT TIME ZONE 'UTC')::date AS date,
        array_agg(DISTINCT source_snapshot_id ORDER BY source_snapshot_id)
            AS source_snapshot_ids
    FROM transaction_source
    CROSS JOIN LATERAL unnest(
        coalesce(transaction_source.source_snapshot_ids, ARRAY[]::text[])
    ) AS source_snapshot_id
    GROUP BY
        transaction_source.tenant_id,
        transaction_source.store_id,
        (transaction_source.event_time AT TIME ZONE 'UTC')::date
),
transaction_daily AS (
    SELECT
        source_txn.tenant_id,
        source_txn.store_id,
        (source_txn.event_time AT TIME ZONE 'UTC')::date AS date,
        sum(source_txn.net_amount)::double precision AS daily_net_revenue,
        count(*)::bigint AS transaction_count,
        max(
            greatest(
                source_txn.observation_time,
                source_txn.ingested_at
            )
        ) AS source_available_at,
        coalesce(
            bool_and(cardinality(source_txn.source_snapshot_ids) > 0),
            FALSE
        )
            AS lineage_complete,
        coalesce(
            bool_and(
                source_txn.observation_time >= source_txn.event_time
                AND source_txn.ingested_at >= source_txn.observation_time
                AND source_txn.source_run_complete
            ),
            FALSE
        ) AS source_run_complete,
        daily_source_ids.source_snapshot_ids,
        max(source_txn.source_run_finished_at) AS source_run_finished_at
    FROM transaction_source AS source_txn
    LEFT JOIN daily_source_ids
        ON daily_source_ids.tenant_id = source_txn.tenant_id
       AND daily_source_ids.store_id = source_txn.store_id
       AND daily_source_ids.date =
            (source_txn.event_time AT TIME ZONE 'UTC')::date
    GROUP BY
        source_txn.tenant_id,
        source_txn.store_id,
        (source_txn.event_time AT TIME ZONE 'UTC')::date,
        daily_source_ids.source_snapshot_ids
),
mature_daily AS (
    SELECT
        transaction_daily.*,
        greatest(
            source_available_at,
            source_run_finished_at,
            (date + 1)::timestamp AT TIME ZONE 'UTC'
        ) AS label_maturity_time
    FROM transaction_daily
),
point_in_time AS (
    SELECT
        target.*,
        causal.revenue_lag_1,
        causal.revenue_lag_7,
        causal.rolling_mean_7,
        causal.rolling_mean_28,
        causal.prior_day_count_28,
        causal.prior_feature_maturity_time,
        lineage_window.source_snapshot_ids AS lineage_window_snapshot_ids
    FROM mature_daily AS target
    LEFT JOIN LATERAL (
        SELECT
            max(prior.daily_net_revenue)
                FILTER (WHERE prior.date = target.date - 1) AS revenue_lag_1,
            max(prior.daily_net_revenue)
                FILTER (WHERE prior.date = target.date - 7) AS revenue_lag_7,
            avg(prior.daily_net_revenue)
                FILTER (WHERE prior.date >= target.date - 7) AS rolling_mean_7,
            avg(prior.daily_net_revenue) AS rolling_mean_28,
            count(*)::integer AS prior_day_count_28,
            max(prior.label_maturity_time) AS prior_feature_maturity_time
        FROM mature_daily AS prior
        WHERE prior.tenant_id = target.tenant_id
          AND prior.store_id = target.store_id
          AND prior.date >= target.date - 28
          AND prior.date < target.date
    ) AS causal ON TRUE
    LEFT JOIN LATERAL (
        SELECT array_agg(DISTINCT snapshot_id ORDER BY snapshot_id)
            AS source_snapshot_ids
        FROM (
            SELECT unnest(coalesce(target.source_snapshot_ids, ARRAY[]::text[]))
                AS snapshot_id
            UNION ALL
            SELECT unnest(coalesce(prior.source_snapshot_ids, ARRAY[]::text[]))
                AS snapshot_id
            FROM mature_daily AS prior
            WHERE prior.tenant_id = target.tenant_id
              AND prior.store_id = target.store_id
              AND prior.date >= target.date - 28
              AND prior.date < target.date
        ) AS lineage_ids
    ) AS lineage_window ON TRUE
)
SELECT
    'forecast_training_view'::text AS view_name,
    'forecast-training-view-v2'::text AS view_version,
    concat(tenant_id::text, ':', store_id::text, ':', date::text) AS entity_id,
    tenant_id,
    store_id,
    date,
    greatest(label_maturity_time, prior_feature_maturity_time)
        AS feature_snapshot_time,
    greatest(label_maturity_time, prior_feature_maturity_time)
        + interval '1 microsecond' AS prediction_origin_time,
    label_maturity_time,
    daily_net_revenue,
    revenue_lag_1,
    revenue_lag_7,
    rolling_mean_7,
    rolling_mean_28,
    lineage_window_snapshot_ids AS source_snapshot_ids,
    CASE
        WHEN lineage_complete AND source_run_complete THEN 1.0
        ELSE 0.0
    END::double precision AS data_quality_score,
    1.0::double precision AS confidence,
    (
        lineage_complete
        AND source_run_complete
        AND cardinality(lineage_window_snapshot_ids) > 0
        AND prior_day_count_28 = 28
        AND revenue_lag_1 IS NOT NULL
        AND revenue_lag_7 IS NOT NULL
        AND rolling_mean_7 IS NOT NULL
        AND rolling_mean_28 IS NOT NULL
        AND label_maturity_time <= CURRENT_TIMESTAMP
        AND greatest(label_maturity_time, prior_feature_maturity_time)
            < greatest(label_maturity_time, prior_feature_maturity_time)
                + interval '1 microsecond'
    ) AS is_training_eligible,
    FALSE AS is_scoring_eligible,
    CASE
        WHEN NOT lineage_complete THEN 'SOURCE_LINEAGE_INCOMPLETE'
        WHEN NOT source_run_complete THEN 'SOURCE_RUN_NOT_COMPLETE'
        WHEN cardinality(lineage_window_snapshot_ids) = 0 THEN 'SOURCE_LINEAGE_MISSING'
        WHEN prior_day_count_28 <> 28 THEN 'INSUFFICIENT_28_DAY_HISTORY'
        WHEN revenue_lag_1 IS NULL OR revenue_lag_7 IS NULL
            THEN 'DAILY_HISTORY_GAP'
        WHEN label_maturity_time > CURRENT_TIMESTAMP THEN 'LABEL_NOT_MATURE'
        ELSE NULL
    END AS exclusion_reason
FROM point_in_time;

COMMENT ON VIEW model_ready.forecast_training_view IS
    'v2: tenant/store daily revenue labels from core.transactions; all features use only prior dates.';

INSERT INTO model_ready.view_contracts (
    relation_name,
    view_name,
    view_version,
    source_relations,
    contract_state,
    training_enabled,
    blocked_reason,
    updated_at
) VALUES
    (
        'model_ready.forecast_training_view',
        'forecast_training_view',
        'forecast-training-view-v2',
        ARRAY[
            'core.transactions',
            'core.stores',
            'data_plane.canonical_lineage',
            'data_plane.ingestion_runs'
        ],
        'ACTIVE',
        TRUE,
        NULL,
        CURRENT_TIMESTAMP
    ),
    (
        'model_ready.valuation_view',
        'valuation_view',
        'valuation-view-v1',
        ARRAY[]::text[],
        'BLOCKED',
        FALSE,
        'MATURE_REALIZED_TRANSACTION_OUTCOME_RELATION_MISSING',
        CURRENT_TIMESTAMP
    ),
    (
        'model_ready.candidate_site_view',
        'candidate_site_view',
        'candidate-site-view-v1',
        ARRAY[]::text[],
        'BLOCKED',
        FALSE,
        'MATURE_CANDIDATE_SITE_OUTCOME_RELATION_MISSING',
        CURRENT_TIMESTAMP
    ),
    (
        'model_ready.avm_liquidity_training_view',
        'avm_liquidity_training_view',
        'avm-liquidity-training-view-v1',
        ARRAY[]::text[],
        'BLOCKED',
        FALSE,
        'MATURE_LIQUIDITY_EVENT_RELATION_MISSING',
        CURRENT_TIMESTAMP
    )
ON CONFLICT (relation_name) DO UPDATE SET
    view_name = EXCLUDED.view_name,
    view_version = EXCLUDED.view_version,
    source_relations = EXCLUDED.source_relations,
    contract_state = EXCLUDED.contract_state,
    training_enabled = EXCLUDED.training_enabled,
    blocked_reason = EXCLUDED.blocked_reason,
    updated_at = CURRENT_TIMESTAMP;
