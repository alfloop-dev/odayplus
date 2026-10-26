# Brand Transfer Data Contract — Field Dictionary

- Task: `ODP-BRAND-TRANSFER-CONTRACT-PREP-001`
- Contract version: `0.1.0-draft`
- Inspected source: `9048161e058becff5a53593a773d3c42238213fb`
- Inspected at: 2026-09-08T15:23Z
- Status: Phase A engineering preparation — statistical definitions require data-owner confirmation

---

## 1. Identity & Scope Fields

| Field | Type | Required | Source | Description | Data-Owner Confirmation Needed |
|---|---|---|---|---|---|
| `contract_version` | string | Yes | System | Schema version identifier. Const `0.1.0-draft` for this iteration. | No |
| `observation_id` | UUID | Yes | Platform-generated | Unique record identifier for each transfer observation. | No |
| `tenant_id` | string | No | Platform config | Multi-tenant discriminator. Empty in single-tenant fixtures. | Confirm multi-tenant requirements |
| `source_brand_id` | UUID | Yes | `core.brands.brand_id` | Brand losing customer share. Must exist in brands master. | Confirm brand scope (owned + competitor + franchise?) |
| `target_brand_id` | UUID | Yes | `core.brands.brand_id` | Brand gaining customer share. Must differ from `source_brand_id`. | Same as above |

### Current State (Inspected)

- `core.brands` table (migration `000001`) contains: `brand_id`, `brand_code`, `brand_name`, `brand_type` (owned/franchise/competitor/external), `brand_capture_group`, `status`.
- No temporal transfer matrix table exists in any migration.
- `geo.competitor_stores` has `brand_name` (text) referencing competitor brands, but NOT linked to `core.brands.brand_id`.

---

## 2. Temporal Fields

| Field | Type | Required | Source | Description | Data-Owner Confirmation Needed |
|---|---|---|---|---|---|
| `observation_period.start` | date (ISO 8601) | Yes | Upstream provider | Inclusive start of observation window. | Confirm typical observation window length |
| `observation_period.end` | date (ISO 8601) | Yes | Upstream provider | Inclusive end of observation window. | Same |
| `observation_period.granularity` | enum | No | Upstream provider | Aggregation granularity: daily, weekly, monthly, quarterly. Default: monthly. | Confirm available granularity from data source |

### Current State (Inspected)

- `brand_transfer_view.sql` uses `b1.created_at` (brand creation time) as `latest_observation_time`, which is semantically incorrect — it reflects when the brand was registered, not when a transfer was observed.
- No time-series transfer data exists anywhere in the repo.

---

## 3. Geography Fields

| Field | Type | Required | Source | Description | Data-Owner Confirmation Needed |
|---|---|---|---|---|---|
| `geography.geo_cell_id` | string | Yes | H3 grid / trade-area system | H3 cell index or polygon ID scoping the observation. | Confirm H3 resolution or trade-area definition |
| `geography.location_type` | enum | No | Derived | Urban/suburban/rural/mixed classification. | Confirm classification source |
| `geography.region_code` | string | No | Administrative data | County/district code. | No |

### Current State (Inspected)

- `brand_transfer_view.sql` hardcodes `location_type = 'urban'` for all rows — no geographic differentiation.
- `geo.h3_cells` table exists and is used by other model-ready views, but `brand_transfer_view` does not reference it.

---

## 4. Store Context Fields (Optional)

| Field | Type | Required | Source | Description | Data-Owner Confirmation Needed |
|---|---|---|---|---|---|
| `store_context.store_id` | UUID | No | `core.stores.store_id` | Store reference if observation is store-scoped. | Confirm if store-level or area-level transfer is measured |
| `store_context.store_format_code` | string | No | `core.stores.store_format_code` | E.g. ODAY_G2, G3_COMPACT, FLAGSHIP. | No |
| `store_context.store_age_bucket` | enum | No | Derived | Cohort by operating age: 0–6m, 6–12m, 12m+. | Confirm bucket definitions |

### Current State (Inspected)

- `brand_transfer_view.sql` hardcodes `store_format_code = 'ODAY_G2'` and `store_age_bucket = '0_6m'` for all rows.
- No actual store-to-transfer linkage exists.

---

## 5. Transfer Metrics Fields

| Field | Type | Required | Source | Description | Data-Owner Confirmation Needed |
|---|---|---|---|---|---|
| `transfer_metrics.transfer_ratio` | number [0, 1] | Yes | **Real measurement** | Fraction of source-brand customers transferring to target brand. **Production value MUST come from real data. Hardcoded 0.15 is forbidden.** | **Yes — statistical definition, formula, and data source** |
| `transfer_metrics.transfer_volume` | integer ≥ 0 or null | No | Real measurement | Absolute transferred customer/transaction count. | Confirm unit: customers, transactions, or visits |
| `transfer_metrics.sample_size` | integer ≥ 0 or null | No | Real measurement | Count of observations underlying the ratio. Null invalidates confidence. | Confirm minimum sample threshold |
| `transfer_metrics.confidence` | number [0, 1] or null | No | Computed from sample | Statistical confidence of the estimate. **Must be computed, never hardcoded to 1.0.** | Confirm confidence method (CI width, bootstrap, etc.) |
| `transfer_metrics.measurement_method` | enum | Yes | Upstream provider | Method: `receipt_panel`, `loyalty_crossover`, `pos_transaction_matching`, `survey_panel`, `model_estimate`, `unknown`. | **Yes — which method(s) are available** |

### Current State (Inspected)

- `brand_transfer_view.sql`: `transfer_ratio = 0.15` (hardcoded constant), `data_quality_score = 1.0` (hardcoded), `confidence = null` (nominal; schema.yml describes it as nullable).
- `MODEL_READY_VIEWS_BASELINE.md` explicitly marks this as a "safe, predictable mock baseline".
- `signal-store/client.py:319`: `brand_transfer_confidence: 0.76` in a mock payload — unused by any production module.

---

## 6. Data Quality Fields

| Field | Type | Required | Source | Description | Data-Owner Confirmation Needed |
|---|---|---|---|---|---|
| `data_quality.data_quality_score` | number [0, 1] or null | No | Computed | Composite quality from sample coverage, recency, method reliability. **Must NOT be 1.0 by default.** | Confirm quality scoring rubric |
| `data_quality.freshness_days` | integer ≥ 0 or null | No | Computed | Days since observation period end. | Confirm acceptable staleness window |
| `data_quality.staleness_threshold_days` | integer ≥ 1 | No | Policy | Max acceptable freshness before observation is stale. Default: 90 days. | Confirm threshold |
| `data_quality.is_training_eligible` | boolean | No | Computed | Whether observation may be used for model training. Default: false. | Confirm training eligibility criteria |
| `data_quality.is_scoring_eligible` | boolean | No | Computed | Whether observation may be used in live scoring. Default: false. | Confirm scoring eligibility criteria |

### Current State (Inspected)

- `brand_transfer_view.sql`: `data_quality_score = 1.0` (unconditional), `is_training_eligible = true`, `is_scoring_eligible = true` — all hardcoded without data backing.
- No freshness tracking or staleness gating exists.

---

## 7. Lineage Fields

| Field | Type | Required | Source | Description | Data-Owner Confirmation Needed |
|---|---|---|---|---|---|
| `lineage.source_reference` | string | Yes | Upstream contract | Canonical ID of the data source (provider contract, dataset version, API endpoint). | **Yes — identify actual data source** |
| `lineage.source_sha` | string or null | No | Computed | SHA-256 of source file or API response for integrity verification. | No |
| `lineage.ingested_at` | datetime (UTC) | Yes | Platform | Timestamp of data ingestion. | No |
| `lineage.pipeline_run_id` | string or null | No | Platform | ETL/ingestion run identifier. | No |

### Current State (Inspected)

- `brand_transfer_view.sql`: `source_snapshot_ids = array['core.brands']` — references only the static brand master, not any transfer data source.
- No ingestion pipeline or run tracking exists for brand transfer data.

---

## 8. Key Constraints & Anti-Patterns

| Rule | Rationale |
|---|---|
| `source_brand_id ≠ target_brand_id` | A brand cannot transfer to itself |
| `transfer_ratio` must come from real data | Hardcoded 0.15 creates false precision in SiteScore |
| `confidence` must be sample-derived | Hardcoded 1.0 masks data absence |
| `data_quality_score` must reflect actual quality | Hardcoded 1.0 violates anti-measurement-defaults policy |
| Missing data → null, not 0 or default | `observed_zero` (measured zero transfer) ≠ `missing` (no data) ≠ `stale` (expired observation) ≠ `insufficient_sample` (below threshold) |

---

## 9. Disambiguation: Four Data-Absence States

| State | Meaning | Contract Representation | Consumer Behavior |
|---|---|---|---|
| `observed_zero` | Transfer was measured and found to be zero | `transfer_ratio = 0.0`, `sample_size > 0`, `confidence > 0` | Valid zero — eligible for scoring |
| `missing` | No data source available for this brand pair | Record absent or `transfer_ratio = null` | Fail-closed: mark unmodelled |
| `stale` | Observation exists but `freshness_days > staleness_threshold_days` | `is_scoring_eligible = false` | Degrade or exclude from scoring |
| `insufficient_sample` | Observation exists but `sample_size` below threshold | `confidence = null` or below minimum | Degrade confidence in output |
