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

CREATE OR REPLACE VIEW model_ready.candidate_site_view AS
WITH authoritative_order_days AS (
    SELECT
        tenant.tenant_id,
        ingestion.partition_key::date AS partition_date,
        max(ingestion.finished_at) AS finished_at,
        array_agg(DISTINCT ingestion.run_id::text ORDER BY ingestion.run_id::text)
            AS run_ids
    FROM data_plane.ingestion_runs AS ingestion
    CROSS JOIN (
        SELECT DISTINCT tenant_id
        FROM core.stores
    ) AS tenant
    WHERE ingestion.source_kind = 'orders'
      AND ingestion.status = 'SUCCEEDED'
      AND ingestion.reconciled
      AND ingestion.partition_complete
      AND ingestion.quarantined_count = 0
      AND ingestion.processed_count = ingestion.valid_loaded
      AND ingestion.finished_at IS NOT NULL
      AND ingestion.partition_key ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
    GROUP BY tenant.tenant_id, ingestion.partition_key::date
),
transaction_source AS (
    SELECT
        store.tenant_id,
        txn.transaction_id,
        txn.store_id,
        txn.event_time,
        txn.observation_time,
        txn.ingested_at,
        txn.net_amount,
        geography.h3_res_9 AS h3_index,
        source.source_snapshot_ids,
        source.lineage_complete,
        source.source_available_at
    FROM core.transactions AS txn
    INNER JOIN core.stores AS store
        ON store.store_id = txn.store_id
    INNER JOIN data_plane.transaction_authority AS authority
        ON authority.transaction_id = txn.transaction_id
       AND authority.source_kind = 'orders'
    INNER JOIN LATERAL (
        SELECT
            place.source_snapshot_id,
            place.run_id,
            place.h3_res_9,
            place.observed_at
        FROM data_plane.place_geography AS place
        WHERE place.tenant_id = store.tenant_id
          AND place.store_id = store.store_id
          AND place.valid_from <= txn.event_time
        ORDER BY place.valid_from DESC, place.observed_at DESC
        LIMIT 1
    ) AS geography ON TRUE
    INNER JOIN data_plane.ingestion_runs AS geography_run
        ON geography_run.run_id = geography.run_id
    LEFT JOIN LATERAL (
        SELECT
            array_append(
                array_agg(
                    DISTINCT lineage.source_snapshot_id::text
                    ORDER BY lineage.source_snapshot_id::text
                ),
                geography.source_snapshot_id::text
            ) AS source_snapshot_ids,
            (
                count(DISTINCT lineage.canonical_table) = 2
                AND bool_and(
                    ingestion.status = 'SUCCEEDED'
                    AND ingestion.finished_at IS NOT NULL
                    AND (
                        lineage.canonical_table <> 'core.transactions'
                        OR (
                            lineage.projected_at >= txn.observation_time
                            AND txn.observation_time >= txn.event_time
                            AND txn.ingested_at >= txn.observation_time
                        )
                    )
                )
                AND geography_run.status = 'SUCCEEDED'
                AND geography_run.finished_at IS NOT NULL
            ) AS lineage_complete,
            max(
                greatest(
                    lineage.projected_at,
                    ingestion.finished_at,
                    txn.observation_time,
                    txn.ingested_at,
                    geography.observed_at,
                    geography_run.finished_at
                )
            ) AS source_available_at
        FROM data_plane.canonical_lineage AS lineage
        INNER JOIN data_plane.ingestion_runs AS ingestion
            ON ingestion.run_id = lineage.run_id
        WHERE lineage.tenant_id = store.tenant_id
          AND (
                (
                    lineage.canonical_table = 'core.transactions'
                    AND lineage.canonical_id = txn.transaction_id
                )
                OR
                (
                    lineage.canonical_table = 'core.stores'
                    AND lineage.canonical_id = store.store_id
                )
          )
    ) AS source ON TRUE
    WHERE txn.transaction_status = 'succeeded'
      AND txn.currency = 'TWD'
),
transaction_snapshot_source AS (
    SELECT
        source_txn.tenant_id,
        source_txn.transaction_id,
        source_txn.store_id,
        source_txn.event_time,
        source_txn.h3_index,
        snapshot_id
    FROM transaction_source AS source_txn
    CROSS JOIN LATERAL unnest(
        coalesce(source_txn.source_snapshot_ids, ARRAY[]::text[])
    ) AS snapshot_id
),
store_anchor AS (
    SELECT
        store.tenant_id,
        store.store_id,
        store.store_format_code AS target_format_code,
        store.opened_on,
        CASE
            WHEN store.opened_on IS NOT NULL
                THEN store.opened_on::timestamp AT TIME ZONE 'UTC'
            ELSE NULL
        END AS feature_cutoff_time,
        geography.latitude::double precision AS latitude,
        geography.longitude::double precision AS longitude,
        geography.geocode_confidence::double precision AS geocode_confidence,
        geography.h3_res_9 AS h3_index,
        identity_source.source_snapshot_ids AS identity_snapshot_ids,
        identity_source.identity_relation_count,
        identity_source.lineage_complete AS identity_lineage_complete,
        identity_source.source_available_at AS identity_available_at
    FROM core.stores AS store
    LEFT JOIN LATERAL (
        SELECT
            place.source_snapshot_id,
            place.run_id,
            place.latitude,
            place.longitude,
            place.geocode_confidence,
            place.h3_res_9,
            place.observed_at
        FROM data_plane.place_geography AS place
        WHERE place.tenant_id = store.tenant_id
          AND place.store_id = store.store_id
          AND store.opened_on IS NOT NULL
          AND place.valid_from <=
                store.opened_on::timestamp AT TIME ZONE 'UTC'
        ORDER BY place.valid_from DESC, place.observed_at DESC
        LIMIT 1
    ) AS geography ON TRUE
    LEFT JOIN data_plane.ingestion_runs AS geography_run
        ON geography_run.run_id = geography.run_id
    LEFT JOIN LATERAL (
        SELECT
            array_append(
                array_agg(
                    DISTINCT lineage.source_snapshot_id::text
                    ORDER BY lineage.source_snapshot_id::text
                ),
                geography.source_snapshot_id::text
            ) AS source_snapshot_ids,
            count(DISTINCT lineage.canonical_table) + 1
                AS identity_relation_count,
            (
                count(DISTINCT lineage.canonical_table) = 1
                AND bool_and(
                    ingestion.status = 'SUCCEEDED'
                    AND ingestion.finished_at IS NOT NULL
                )
                AND geography_run.status = 'SUCCEEDED'
                AND geography_run.finished_at IS NOT NULL
            ) AS lineage_complete,
            max(greatest(
                lineage.projected_at,
                ingestion.finished_at,
                geography.observed_at,
                geography_run.finished_at
            ))
                AS source_available_at
        FROM data_plane.canonical_lineage AS lineage
        INNER JOIN data_plane.ingestion_runs AS ingestion
            ON ingestion.run_id = lineage.run_id
        WHERE lineage.tenant_id = store.tenant_id
          AND (
                (
                    lineage.canonical_table = 'core.stores'
                    AND lineage.canonical_id = store.store_id
                )
          )
    ) AS identity_source ON TRUE
),
evaluated AS (
    SELECT
        anchor.*,
        prior.prior_90d_cell_net_revenue,
        prior.prior_90d_cell_transaction_count,
        prior.prior_90d_cell_store_count,
        prior_snapshots.source_snapshot_ids AS prior_snapshot_ids,
        prior.lineage_complete AS prior_lineage_complete,
        prior.source_available_at AS prior_available_at,
        label.realized_90d_net_revenue,
        label.label_transaction_count,
        label_snapshots.source_snapshot_ids AS label_snapshot_ids,
        label.lineage_complete AS label_lineage_complete,
        label.source_available_at AS label_available_at,
        prior_coverage.covered_days AS prior_covered_days,
        prior_coverage.run_ids AS prior_run_ids,
        prior_coverage.source_available_at AS prior_run_available_at,
        label_coverage.covered_days AS label_covered_days,
        label_coverage.run_ids AS label_run_ids,
        label_coverage.source_available_at AS label_run_available_at
    FROM store_anchor AS anchor
    LEFT JOIN LATERAL (
        SELECT
            coalesce(sum(source_txn.net_amount), 0)::double precision
                AS prior_90d_cell_net_revenue,
            count(*)::bigint AS prior_90d_cell_transaction_count,
            count(DISTINCT source_txn.store_id)::integer
                AS prior_90d_cell_store_count,
            (
                count(*) > 0
                AND bool_and(
                    source_txn.lineage_complete
                    AND source_txn.source_available_at < anchor.feature_cutoff_time
                )
            ) AS lineage_complete,
            max(source_txn.source_available_at) AS source_available_at
        FROM transaction_source AS source_txn
        WHERE source_txn.tenant_id = anchor.tenant_id
          AND source_txn.h3_index = anchor.h3_index
          AND source_txn.store_id <> anchor.store_id
          AND source_txn.event_time >=
                anchor.feature_cutoff_time - interval '90 days'
          AND source_txn.event_time < anchor.feature_cutoff_time
    ) AS prior ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            array_agg(DISTINCT snapshot_id ORDER BY snapshot_id)
                AS source_snapshot_ids
        FROM transaction_snapshot_source AS snapshot
        WHERE snapshot.tenant_id = anchor.tenant_id
          AND snapshot.h3_index = anchor.h3_index
          AND snapshot.store_id <> anchor.store_id
          AND snapshot.event_time >=
                anchor.feature_cutoff_time - interval '90 days'
          AND snapshot.event_time < anchor.feature_cutoff_time
    ) AS prior_snapshots ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            coalesce(sum(source_txn.net_amount), 0)::double precision
                AS realized_90d_net_revenue,
            count(*)::bigint AS label_transaction_count,
            (
                count(*) = 0
                OR bool_and(source_txn.lineage_complete)
            ) AS lineage_complete,
            max(source_txn.source_available_at) AS source_available_at
        FROM transaction_source AS source_txn
        WHERE source_txn.tenant_id = anchor.tenant_id
          AND source_txn.store_id = anchor.store_id
          AND source_txn.event_time >= anchor.feature_cutoff_time
          AND source_txn.event_time <
                anchor.feature_cutoff_time + interval '90 days'
    ) AS label ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            array_agg(DISTINCT snapshot_id ORDER BY snapshot_id)
                AS source_snapshot_ids
        FROM transaction_snapshot_source AS snapshot
        WHERE snapshot.tenant_id = anchor.tenant_id
          AND snapshot.store_id = anchor.store_id
          AND snapshot.event_time >= anchor.feature_cutoff_time
          AND snapshot.event_time <
                anchor.feature_cutoff_time + interval '90 days'
    ) AS label_snapshots ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            count(DISTINCT day.partition_date)::integer AS covered_days,
            array_agg(DISTINCT run_id ORDER BY run_id) AS run_ids,
            max(day.finished_at) AS source_available_at
        FROM authoritative_order_days AS day
        LEFT JOIN LATERAL unnest(day.run_ids) AS run_id ON TRUE
        WHERE day.tenant_id = anchor.tenant_id
          AND day.partition_date >= anchor.opened_on - 90
          AND day.partition_date < anchor.opened_on
          AND day.finished_at < anchor.feature_cutoff_time
    ) AS prior_coverage ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            count(DISTINCT day.partition_date)::integer AS covered_days,
            array_agg(DISTINCT run_id ORDER BY run_id) AS run_ids,
            max(day.finished_at) AS source_available_at
        FROM authoritative_order_days AS day
        LEFT JOIN LATERAL unnest(day.run_ids) AS run_id ON TRUE
        WHERE day.tenant_id = anchor.tenant_id
          AND day.partition_date >= anchor.opened_on
          AND day.partition_date < anchor.opened_on + 90
    ) AS label_coverage ON TRUE
),
materialized AS (
    SELECT
        evaluated.*,
        greatest(
            identity_available_at,
            prior_available_at,
            prior_run_available_at
        ) AS feature_snapshot_time,
        CASE
            WHEN opened_on IS NULL THEN NULL
            ELSE greatest(
                feature_cutoff_time + interval '90 days',
                label_available_at,
                label_run_available_at
            )
        END AS label_maturity_time,
        provenance.source_snapshot_ids
    FROM evaluated
    LEFT JOIN LATERAL (
        SELECT array_agg(DISTINCT snapshot_id ORDER BY snapshot_id)
            AS source_snapshot_ids
        FROM (
            SELECT unnest(
                coalesce(evaluated.identity_snapshot_ids, ARRAY[]::text[])
            ) AS snapshot_id
            UNION ALL
            SELECT unnest(
                coalesce(evaluated.prior_snapshot_ids, ARRAY[]::text[])
            ) AS snapshot_id
            UNION ALL
            SELECT unnest(
                coalesce(evaluated.label_snapshot_ids, ARRAY[]::text[])
            ) AS snapshot_id
        ) AS snapshots
    ) AS provenance ON TRUE
)
SELECT
    'candidate_site_view'::text AS view_name,
    'candidate-site-view-v2'::text AS view_version,
    concat(tenant_id::text, ':', store_id::text) AS entity_id,
    tenant_id,
    store_id,
    target_format_code,
    opened_on,
    feature_cutoff_time,
    feature_snapshot_time,
    feature_cutoff_time AS prediction_origin_time,
    label_maturity_time,
    identity_available_at AS feature_identity_available_at,
    prior_available_at AS feature_transaction_available_at,
    prior_run_available_at AS feature_partition_available_at,
    90::integer AS label_horizon_days,
    realized_90d_net_revenue,
    label_transaction_count,
    latitude,
    longitude,
    geocode_confidence,
    h3_index,
    prior_90d_cell_net_revenue,
    prior_90d_cell_transaction_count,
    prior_90d_cell_store_count,
    prior_run_ids AS feature_run_ids,
    label_run_ids,
    source_snapshot_ids,
    CASE
        WHEN identity_lineage_complete
         AND prior_lineage_complete
         AND label_lineage_complete
         AND prior_covered_days = 90
         AND label_covered_days = 90
            THEN 1.0
        ELSE 0.0
    END::double precision AS data_quality_score,
    least(coalesce(geocode_confidence, 0.0), 1.0)::double precision
        AS confidence,
    (
        opened_on IS NOT NULL
        AND target_format_code IS NOT NULL
        AND h3_index IS NOT NULL
        AND latitude IS NOT NULL
        AND longitude IS NOT NULL
        AND identity_relation_count = 2
        AND identity_lineage_complete
        AND identity_available_at < feature_cutoff_time
        AND prior_covered_days = 90
        AND prior_lineage_complete
        AND prior_available_at < feature_cutoff_time
        AND prior_run_available_at < feature_cutoff_time
        AND feature_snapshot_time < feature_cutoff_time
        AND prior_90d_cell_transaction_count > 0
        AND label_covered_days = 90
        AND label_lineage_complete
        AND label_maturity_time <= CURRENT_TIMESTAMP
        AND cardinality(source_snapshot_ids) > 0
    ) AS is_training_eligible,
    FALSE AS is_scoring_eligible,
    CASE
        WHEN opened_on IS NULL THEN 'OPENED_ON_MISSING'
        WHEN target_format_code IS NULL THEN 'TARGET_FORMAT_MISSING'
        WHEN h3_index IS NULL OR latitude IS NULL OR longitude IS NULL
            THEN 'GEO_IDENTITY_MISSING'
        WHEN identity_relation_count <> 2 OR NOT identity_lineage_complete
            THEN 'IDENTITY_LINEAGE_INCOMPLETE'
        WHEN identity_available_at >= feature_cutoff_time
            THEN 'IDENTITY_NOT_POINT_IN_TIME'
        WHEN prior_covered_days <> 90 THEN 'PRIOR_90D_PARTITION_COVERAGE_INCOMPLETE'
        WHEN NOT prior_lineage_complete
          OR prior_available_at >= feature_cutoff_time
          OR prior_run_available_at >= feature_cutoff_time
          OR feature_snapshot_time >= feature_cutoff_time
            THEN 'PRIOR_FEATURE_LINEAGE_NOT_POINT_IN_TIME'
        WHEN prior_90d_cell_transaction_count = 0
            THEN 'PRIOR_CELL_HISTORY_MISSING'
        WHEN label_covered_days <> 90 THEN 'LABEL_90D_PARTITION_COVERAGE_INCOMPLETE'
        WHEN NOT label_lineage_complete THEN 'LABEL_LINEAGE_INCOMPLETE'
        WHEN label_maturity_time > CURRENT_TIMESTAMP THEN 'LABEL_NOT_MATURE'
        WHEN cardinality(source_snapshot_ids) = 0 THEN 'SOURCE_LINEAGE_MISSING'
        ELSE NULL
    END AS exclusion_reason
FROM materialized;

COMMENT ON VIEW model_ready.candidate_site_view IS
    'v2: historical opened-store SiteScore rows. Label is realized TWD net revenue in the complete 90-day post-opening horizon, including legitimate zero outcomes; features and immutable geography evidence must be available strictly before opening.';

CREATE OR REPLACE VIEW model_ready.heatzone_training_view AS
WITH authoritative_order_days AS (
    SELECT
        tenant.tenant_id,
        ingestion.partition_key::date AS partition_date,
        max(ingestion.finished_at) AS finished_at,
        array_agg(DISTINCT ingestion.run_id::text ORDER BY ingestion.run_id::text)
            AS run_ids
    FROM data_plane.ingestion_runs AS ingestion
    CROSS JOIN (
        SELECT DISTINCT tenant_id
        FROM core.stores
    ) AS tenant
    WHERE ingestion.source_kind = 'orders'
      AND ingestion.status = 'SUCCEEDED'
      AND ingestion.reconciled
      AND ingestion.partition_complete
      AND ingestion.quarantined_count = 0
      AND ingestion.processed_count = ingestion.valid_loaded
      AND ingestion.finished_at IS NOT NULL
      AND ingestion.partition_key ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
    GROUP BY tenant.tenant_id, ingestion.partition_key::date
),
store_source AS (
    SELECT
        store.tenant_id,
        store.store_id,
        store.opened_on,
        identity_source.source_snapshot_ids,
        identity_source.identity_relation_count,
        identity_source.lineage_complete,
        identity_source.source_available_at
    FROM core.stores AS store
    LEFT JOIN LATERAL (
        SELECT
            array_agg(
                DISTINCT lineage.source_snapshot_id::text
                ORDER BY lineage.source_snapshot_id::text
            ) AS source_snapshot_ids,
            count(DISTINCT lineage.canonical_table) AS identity_relation_count,
            (
                count(DISTINCT lineage.canonical_table) = 1
                AND bool_and(
                    ingestion.status = 'SUCCEEDED'
                    AND ingestion.finished_at IS NOT NULL
                )
            ) AS lineage_complete,
            max(greatest(lineage.projected_at, ingestion.finished_at))
                AS source_available_at
        FROM data_plane.canonical_lineage AS lineage
        INNER JOIN data_plane.ingestion_runs AS ingestion
            ON ingestion.run_id = lineage.run_id
        WHERE lineage.tenant_id = store.tenant_id
          AND lineage.canonical_table = 'core.stores'
          AND lineage.canonical_id = store.store_id
    ) AS identity_source ON TRUE
    WHERE store.opened_on IS NOT NULL
),
transaction_source AS (
    SELECT
        store.tenant_id,
        txn.transaction_id,
        txn.store_id,
        txn.event_time,
        txn.observation_time,
        txn.ingested_at,
        txn.net_amount,
        geography.h3_res_9 AS h3_index,
        source.source_snapshot_ids,
        source.lineage_complete,
        source.source_available_at
    FROM core.transactions AS txn
    INNER JOIN core.stores AS store
        ON store.store_id = txn.store_id
    INNER JOIN data_plane.transaction_authority AS authority
        ON authority.transaction_id = txn.transaction_id
       AND authority.source_kind = 'orders'
    INNER JOIN LATERAL (
        SELECT
            place.source_snapshot_id,
            place.run_id,
            place.h3_res_9,
            place.observed_at
        FROM data_plane.place_geography AS place
        WHERE place.tenant_id = store.tenant_id
          AND place.store_id = store.store_id
          AND place.valid_from <= txn.event_time
        ORDER BY place.valid_from DESC, place.observed_at DESC
        LIMIT 1
    ) AS geography ON TRUE
    INNER JOIN data_plane.ingestion_runs AS geography_run
        ON geography_run.run_id = geography.run_id
    LEFT JOIN LATERAL (
        SELECT
            array_append(
                array_agg(
                    DISTINCT lineage.source_snapshot_id::text
                    ORDER BY lineage.source_snapshot_id::text
                ),
                geography.source_snapshot_id::text
            ) AS source_snapshot_ids,
            (
                count(DISTINCT lineage.canonical_table) = 2
                AND bool_and(
                    ingestion.status = 'SUCCEEDED'
                    AND ingestion.finished_at IS NOT NULL
                    AND (
                        lineage.canonical_table <> 'core.transactions'
                        OR (
                            lineage.projected_at >= txn.observation_time
                            AND txn.observation_time >= txn.event_time
                            AND txn.ingested_at >= txn.observation_time
                        )
                    )
                )
                AND geography_run.status = 'SUCCEEDED'
                AND geography_run.finished_at IS NOT NULL
            ) AS lineage_complete,
            max(
                greatest(
                    lineage.projected_at,
                    ingestion.finished_at,
                    txn.observation_time,
                    txn.ingested_at,
                    geography.observed_at,
                    geography_run.finished_at
                )
            ) AS source_available_at
        FROM data_plane.canonical_lineage AS lineage
        INNER JOIN data_plane.ingestion_runs AS ingestion
            ON ingestion.run_id = lineage.run_id
        WHERE lineage.tenant_id = store.tenant_id
          AND (
                (
                    lineage.canonical_table = 'core.transactions'
                    AND lineage.canonical_id = txn.transaction_id
                )
                OR
                (
                    lineage.canonical_table = 'core.stores'
                    AND lineage.canonical_id = store.store_id
                )
          )
    ) AS source ON TRUE
    WHERE txn.transaction_status = 'succeeded'
      AND txn.currency = 'TWD'
      AND geography.h3_res_9 IS NOT NULL
),
transaction_snapshot_source AS (
    SELECT
        source_txn.tenant_id,
        source_txn.transaction_id,
        source_txn.store_id,
        source_txn.event_time,
        source_txn.h3_index,
        snapshot_id
    FROM transaction_source AS source_txn
    CROSS JOIN LATERAL unnest(
        coalesce(source_txn.source_snapshot_ids, ARRAY[]::text[])
    ) AS snapshot_id
),
cell_origin AS (
    SELECT
        store_source.tenant_id,
        geography.h3_res_9 AS h3_index,
        day.partition_date AS origin_date,
        day.partition_date::timestamp AT TIME ZONE 'UTC'
            AS feature_cutoff_time,
        min(geography.city) AS admin_city,
        min(geography.district) AS admin_district,
        avg(geography.latitude)::double precision AS cell_latitude,
        avg(geography.longitude)::double precision AS cell_longitude,
        avg(geography.geocode_confidence)::double precision
            AS average_geocode_confidence,
        count(DISTINCT store_source.store_id)::integer
            AS prior_opened_store_count,
        bool_and(
            store_source.identity_relation_count = 1
            AND store_source.lineage_complete
            AND geography_run.status = 'SUCCEEDED'
            AND geography_run.finished_at IS NOT NULL
            AND greatest(
                store_source.source_available_at,
                geography.observed_at,
                geography_run.finished_at
            ) <
                day.partition_date::timestamp AT TIME ZONE 'UTC'
        ) AS identity_lineage_complete,
        max(greatest(
            store_source.source_available_at,
            geography.observed_at,
            geography_run.finished_at
        )) AS identity_available_at,
        array_agg(
            DISTINCT snapshot_id
            ORDER BY snapshot_id
        ) AS identity_snapshot_ids
    FROM authoritative_order_days AS day
    INNER JOIN store_source
        ON store_source.tenant_id = day.tenant_id
       AND store_source.opened_on < day.partition_date
    INNER JOIN LATERAL (
        SELECT
            place.source_snapshot_id,
            place.run_id,
            place.h3_res_9,
            place.city,
            place.district,
            place.latitude,
            place.longitude,
            place.geocode_confidence,
            place.observed_at
        FROM data_plane.place_geography AS place
        WHERE place.tenant_id = store_source.tenant_id
          AND place.store_id = store_source.store_id
          AND place.valid_from <=
                day.partition_date::timestamp AT TIME ZONE 'UTC'
        ORDER BY place.valid_from DESC, place.observed_at DESC
        LIMIT 1
    ) AS geography ON TRUE
    INNER JOIN data_plane.ingestion_runs AS geography_run
        ON geography_run.run_id = geography.run_id
    CROSS JOIN LATERAL unnest(
        array_append(
            coalesce(store_source.source_snapshot_ids, ARRAY[]::text[]),
            geography.source_snapshot_id::text
        )
    ) AS snapshot_id
    WHERE geography.h3_res_9 IS NOT NULL
    GROUP BY
        store_source.tenant_id,
        geography.h3_res_9,
        day.partition_date
),
evaluated AS (
    SELECT
        origin.*,
        prior.prior_28d_cell_net_revenue,
        prior.prior_90d_cell_net_revenue,
        prior.prior_28d_transaction_count,
        prior.prior_90d_transaction_count,
        prior.prior_90d_transaction_days,
        prior_snapshots.source_snapshot_ids AS prior_snapshot_ids,
        prior.lineage_complete AS prior_lineage_complete,
        prior.source_available_at AS prior_available_at,
        label.realized_28d_cell_net_revenue,
        label.label_transaction_count,
        label_snapshots.source_snapshot_ids AS label_snapshot_ids,
        label.lineage_complete AS label_lineage_complete,
        label.source_available_at AS label_available_at,
        prior_coverage.covered_days AS prior_covered_days,
        prior_coverage.run_ids AS prior_run_ids,
        prior_coverage.source_available_at AS prior_run_available_at,
        label_coverage.covered_days AS label_covered_days,
        label_coverage.run_ids AS label_run_ids,
        label_coverage.source_available_at AS label_run_available_at
    FROM cell_origin AS origin
    LEFT JOIN LATERAL (
        SELECT
            coalesce(
                sum(source_txn.net_amount)
                FILTER (
                    WHERE source_txn.event_time >=
                        origin.feature_cutoff_time - interval '28 days'
                ),
                0
            )::double precision AS prior_28d_cell_net_revenue,
            coalesce(sum(source_txn.net_amount), 0)::double precision
                AS prior_90d_cell_net_revenue,
            count(*)
                FILTER (
                    WHERE source_txn.event_time >=
                        origin.feature_cutoff_time - interval '28 days'
                )::bigint AS prior_28d_transaction_count,
            count(*)::bigint AS prior_90d_transaction_count,
            count(
                DISTINCT (source_txn.event_time AT TIME ZONE 'UTC')::date
            )::integer AS prior_90d_transaction_days,
            (
                count(*) > 0
                AND bool_and(
                    source_txn.lineage_complete
                    AND source_txn.source_available_at <
                        origin.feature_cutoff_time
                )
            ) AS lineage_complete,
            max(source_txn.source_available_at) AS source_available_at
        FROM transaction_source AS source_txn
        WHERE source_txn.tenant_id = origin.tenant_id
          AND source_txn.h3_index = origin.h3_index
          AND source_txn.event_time >=
                origin.feature_cutoff_time - interval '90 days'
          AND source_txn.event_time < origin.feature_cutoff_time
    ) AS prior ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            array_agg(DISTINCT snapshot_id ORDER BY snapshot_id)
                AS source_snapshot_ids
        FROM transaction_snapshot_source AS snapshot
        WHERE snapshot.tenant_id = origin.tenant_id
          AND snapshot.h3_index = origin.h3_index
          AND snapshot.event_time >=
                origin.feature_cutoff_time - interval '90 days'
          AND snapshot.event_time < origin.feature_cutoff_time
    ) AS prior_snapshots ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            coalesce(sum(source_txn.net_amount), 0)::double precision
                AS realized_28d_cell_net_revenue,
            count(*)::bigint AS label_transaction_count,
            (
                count(*) = 0
                OR bool_and(source_txn.lineage_complete)
            ) AS lineage_complete,
            max(source_txn.source_available_at) AS source_available_at
        FROM transaction_source AS source_txn
        WHERE source_txn.tenant_id = origin.tenant_id
          AND source_txn.h3_index = origin.h3_index
          AND source_txn.event_time >= origin.feature_cutoff_time
          AND source_txn.event_time <
                origin.feature_cutoff_time + interval '28 days'
    ) AS label ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            array_agg(DISTINCT snapshot_id ORDER BY snapshot_id)
                AS source_snapshot_ids
        FROM transaction_snapshot_source AS snapshot
        WHERE snapshot.tenant_id = origin.tenant_id
          AND snapshot.h3_index = origin.h3_index
          AND snapshot.event_time >= origin.feature_cutoff_time
          AND snapshot.event_time <
                origin.feature_cutoff_time + interval '28 days'
    ) AS label_snapshots ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            count(DISTINCT day.partition_date)::integer AS covered_days,
            array_agg(DISTINCT run_id ORDER BY run_id) AS run_ids,
            max(day.finished_at) AS source_available_at
        FROM authoritative_order_days AS day
        LEFT JOIN LATERAL unnest(day.run_ids) AS run_id ON TRUE
        WHERE day.tenant_id = origin.tenant_id
          AND day.partition_date >= origin.origin_date - 90
          AND day.partition_date < origin.origin_date
          AND day.finished_at < origin.feature_cutoff_time
    ) AS prior_coverage ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            count(DISTINCT day.partition_date)::integer AS covered_days,
            array_agg(DISTINCT run_id ORDER BY run_id) AS run_ids,
            max(day.finished_at) AS source_available_at
        FROM authoritative_order_days AS day
        LEFT JOIN LATERAL unnest(day.run_ids) AS run_id ON TRUE
        WHERE day.tenant_id = origin.tenant_id
          AND day.partition_date >= origin.origin_date
          AND day.partition_date < origin.origin_date + 28
    ) AS label_coverage ON TRUE
),
materialized AS (
    SELECT
        evaluated.*,
        greatest(
            identity_available_at,
            prior_available_at,
            prior_run_available_at
        ) AS feature_snapshot_time,
        greatest(
            feature_cutoff_time + interval '28 days',
            label_available_at,
            label_run_available_at
        ) AS label_maturity_time,
        provenance.source_snapshot_ids
    FROM evaluated
    LEFT JOIN LATERAL (
        SELECT array_agg(DISTINCT snapshot_id ORDER BY snapshot_id)
            AS source_snapshot_ids
        FROM (
            SELECT unnest(
                coalesce(evaluated.identity_snapshot_ids, ARRAY[]::text[])
            ) AS snapshot_id
            UNION ALL
            SELECT unnest(
                coalesce(evaluated.prior_snapshot_ids, ARRAY[]::text[])
            ) AS snapshot_id
            UNION ALL
            SELECT unnest(
                coalesce(evaluated.label_snapshot_ids, ARRAY[]::text[])
            ) AS snapshot_id
        ) AS snapshots
    ) AS provenance ON TRUE
)
SELECT
    'heatzone_training_view'::text AS view_name,
    'heatzone-training-view-v2'::text AS view_version,
    concat(tenant_id::text, ':', h3_index, ':', origin_date::text) AS entity_id,
    tenant_id,
    h3_index,
    9::integer AS h3_resolution,
    origin_date,
    feature_cutoff_time,
    feature_snapshot_time,
    feature_cutoff_time AS prediction_origin_time,
    label_maturity_time,
    identity_available_at AS feature_identity_available_at,
    prior_available_at AS feature_transaction_available_at,
    prior_run_available_at AS feature_partition_available_at,
    28::integer AS label_horizon_days,
    realized_28d_cell_net_revenue,
    label_transaction_count,
    cell_latitude,
    cell_longitude,
    average_geocode_confidence,
    prior_opened_store_count,
    prior_28d_cell_net_revenue,
    prior_90d_cell_net_revenue,
    prior_28d_transaction_count,
    prior_90d_transaction_count,
    prior_90d_transaction_days,
    admin_city,
    admin_district,
    prior_run_ids AS feature_run_ids,
    label_run_ids,
    source_snapshot_ids,
    CASE
        WHEN identity_lineage_complete
         AND prior_lineage_complete
         AND label_lineage_complete
         AND prior_covered_days = 90
         AND label_covered_days = 28
            THEN 1.0
        ELSE 0.0
    END::double precision AS data_quality_score,
    least(coalesce(average_geocode_confidence, 0.0), 1.0)::double precision
        AS confidence,
    (
        h3_index IS NOT NULL
        AND cell_latitude IS NOT NULL
        AND cell_longitude IS NOT NULL
        AND identity_lineage_complete
        AND identity_available_at < feature_cutoff_time
        AND prior_covered_days = 90
        AND prior_lineage_complete
        AND prior_available_at < feature_cutoff_time
        AND prior_run_available_at < feature_cutoff_time
        AND feature_snapshot_time < feature_cutoff_time
        AND prior_90d_transaction_count > 0
        AND label_covered_days = 28
        AND label_lineage_complete
        AND label_maturity_time <= CURRENT_TIMESTAMP
        AND cardinality(source_snapshot_ids) > 0
    ) AS is_training_eligible,
    FALSE AS is_scoring_eligible,
    CASE
        WHEN h3_index IS NULL OR cell_latitude IS NULL OR cell_longitude IS NULL
            THEN 'GEO_IDENTITY_MISSING'
        WHEN NOT identity_lineage_complete
          OR identity_available_at >= feature_cutoff_time
            THEN 'IDENTITY_LINEAGE_NOT_POINT_IN_TIME'
        WHEN prior_covered_days <> 90 THEN 'PRIOR_90D_PARTITION_COVERAGE_INCOMPLETE'
        WHEN NOT prior_lineage_complete
          OR prior_available_at >= feature_cutoff_time
          OR prior_run_available_at >= feature_cutoff_time
          OR feature_snapshot_time >= feature_cutoff_time
            THEN 'PRIOR_FEATURE_LINEAGE_NOT_POINT_IN_TIME'
        WHEN prior_90d_transaction_count = 0 THEN 'PRIOR_CELL_HISTORY_MISSING'
        WHEN label_covered_days <> 28 THEN 'LABEL_28D_PARTITION_COVERAGE_INCOMPLETE'
        WHEN NOT label_lineage_complete THEN 'LABEL_LINEAGE_INCOMPLETE'
        WHEN label_maturity_time > CURRENT_TIMESTAMP THEN 'LABEL_NOT_MATURE'
        WHEN cardinality(source_snapshot_ids) = 0 THEN 'SOURCE_LINEAGE_MISSING'
        ELSE NULL
    END AS exclusion_reason
FROM materialized;

COMMENT ON VIEW model_ready.heatzone_training_view IS
    'v2: tenant/H3/date HeatZone rows. Label is realized TWD cell net revenue, including legitimate zero outcomes, in the next 28 complete authoritative-order days; every feature and H3 assignment uses immutable evidence available strictly before origin.';

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
        'candidate-site-view-v2',
        ARRAY[
            'core.stores',
            'core.transactions',
            'data_plane.canonical_lineage',
            'data_plane.ingestion_runs',
            'data_plane.place_geography',
            'data_plane.transaction_authority'
        ],
        'ACTIVE',
        TRUE,
        NULL,
        CURRENT_TIMESTAMP
    ),
    (
        'model_ready.heatzone_training_view',
        'heatzone_training_view',
        'heatzone-training-view-v2',
        ARRAY[
            'core.stores',
            'core.transactions',
            'data_plane.canonical_lineage',
            'data_plane.ingestion_runs',
            'data_plane.place_geography',
            'data_plane.transaction_authority'
        ],
        'ACTIVE',
        TRUE,
        NULL,
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
