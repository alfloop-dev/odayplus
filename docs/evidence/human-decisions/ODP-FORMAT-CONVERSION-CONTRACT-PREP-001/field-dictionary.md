# Field Dictionary — Store Format Conversion Event Contract

- **Task**: `ODP-FORMAT-CONVERSION-CONTRACT-PREP-001`
- **Requirement**: `ODP-FR-SITE-001 / FORMAT_CONVERSION`
- **Status**: DRAFT — Stage A engineering preparation
- **Date**: 2026-09-08
- **Source**: [event-contract-draft.json](event-contract-draft.json)

> [!IMPORTANT]
> This dictionary describes the **draft** event schema for brownfield store format conversions. No producer exists in the repository. Stage B requires H04 human input (real events, financial parameters, and operational playbook).

## Required Fields

| # | Field | Type | Format | Description | Source / Owner | Inspected Evidence |
|---|-------|------|--------|-------------|---------------|-------------------|
| 1 | `event_id` | string | UUID | Unique event identifier. Replay with same `event_id` MUST NOT cause duplicate cost/revenue entries. | System-generated | No producer exists; event identity design based on existing `core.stores` UUID pattern (000001 migration) |
| 2 | `event_type` | string | const `FORMAT_CONVERSION` | Event type discriminator for routing. | System constant | Aligns with `ODP-FR-SITE-001` member name in `set_valued_requirements.json` |
| 3 | `store_id` | string | UUID | FK → `core.stores(store_id)`. The existing store undergoing conversion. | Store Operations ERP / manual entry | `core.stores` table in `000001_baseline_canonical_schema.sql:L79-90` |
| 4 | `tenant_id` | string | UUID | Multi-tenant isolation key. | Tenant registry | `core.stores.tenant_id` in `000001_baseline_canonical_schema.sql:L80` |
| 5 | `from_format_code` | string | min 1 char | Original format before conversion (e.g., `ODAY_G1`). Must be a registered code in `TargetFormatRegistry`. | Store Operations | `TargetFormatRegistry` in `modules/site_economics/domain/formats.py:L400-444` registers ODAY_G2, ODAY_G3_COMPACT, ODAY_FLAGSHIP |
| 6 | `to_format_code` | string | min 1 char | Target format after conversion. Must differ from `from_format_code`. | Store Operations / Site Economics | Same registry; `find_best_format_for_area()` selects new-store formats only — conversion selection logic does not yet exist |
| 7 | `conversion_start_date` | string | ISO date | Date remodeling begins. Store assumed closed from this date. | Store Operations playbook | **No playbook exists in repo** — required from H04 |
| 8 | `remodeling_capex` | number | ≥ 0 | Total conversion capital expenditure (base currency). Separate from original greenfield capex. | Finance / Real Estate | `simulator.py` currently models greenfield capex only (`SimulationInput.custom_equipment_capex`, `custom_fitout_capex`) — no conversion capex path |
| 9 | `downtime_days` | integer | ≥ 0 | Calendar days store is closed during remodeling. | Store Operations playbook | **No downtime model in repo** — `simulator.py` has no downtime loss calculation |
| 10 | `event_source` | string | min 1 char | System/process that produced this event (lineage). | Producer system | No producer exists in repo |
| 11 | `event_source_version` | string | min 1 char | Version of the producing system/schema. | Producer system | No producer exists in repo |
| 12 | `recorded_at` | string | ISO datetime | UTC timestamp when event was recorded. | System clock | Standard event envelope field |

## Optional Fields

| # | Field | Type | Format | Description | Source / Owner | Why Optional |
|---|-------|------|--------|-------------|---------------|-------------|
| 13 | `conversion_completed_date` | string \| null | ISO date | Date remodeling completed and store reopens. | Store Operations | Null for in-progress or planned conversions |
| 14 | `residual_value` | number \| null | ≥ 0 | Salvage value of old-format equipment/fixtures. **Null ≠ zero**: null means unknown, zero means explicitly confirmed zero. | Finance / Asset Management | Requires asset depreciation data; may not be available at event creation time |
| 15 | `disposal_cost` | number \| null | ≥ 0 | Cost of disposing old equipment. **Null ≠ zero**: same rule as `residual_value`. | Finance / Asset Management | Same as residual_value |
| 16 | `effective_date` | string \| null | ISO date | Date store operates in new format for financial reporting. May differ from completion date. | Finance | Only needed when soft-opening or regulatory delay applies |
| 17 | `closure_start_date` | string \| null | ISO date | Date store stops trading (may precede physical remodeling). | Store Operations | Defaults to `conversion_start_date` if not separate |
| 18 | `closure_end_date` | string \| null | ISO date | Date store resumes trading in new format. | Store Operations | May differ from `conversion_completed_date` if soft-launch |
| 19 | `ramp_months` | integer \| null | ≥ 0 | Months for converted store to reach steady-state revenue. | Site Economics model | Null means ramp unknown — must be requested via H04 |
| 20 | `ramp_curve_id` | string \| null | — | Reference to applicable `RampCurveSpec`. **Null means curve unknown**: MUST NOT default to greenfield target format ramp; remains unquantified unless an explicit approved conversion-compatible curve reference is provided. | Site Economics model | Requires approved conversion ramp curve; null means unquantified |
| 21 | `daily_baseline_revenue` | number \| null | ≥ 0 | Pre-conversion average daily revenue for loss calculation. | Historical revenue data | Null if historical data unavailable — loss unquantified |
| 22 | `conversion_reason` | string \| null | — | Business justification (UPGRADE, DOWNSIZE, REBRAND, etc.). | Store Operations | Informational; not used in financial calculation |
| 23 | `approved_by` | string \| null | — | Identity of approving human authority. | Governance | Required in Stage B production; null in draft |
| 24 | `idempotency_key` | string \| null | — | Secondary dedup key for at-least-once processing. | Producer system | Some producers may not generate secondary keys |
| 25 | `metadata` | object \| null | — | Extensible envelope. Must not duplicate top-level fields. | Various | Future extensibility |

## Key Design Distinctions

### New Store Selection vs. Brownfield Conversion

| Aspect | New Store (Greenfield) | Format Conversion (Brownfield) |
|--------|----------------------|-------------------------------|
| Existing code | `SimulationInput` in `simulator.py` | **None** — this contract is the draft |
| Format selection | `TargetFormatRegistry.find_best_format_for_area()` | Requires `from_format_code` → `to_format_code` transition |
| Capex | Equipment + fitout for new store | Remodeling capex (demolition + new construction + equipment delta) |
| Revenue impact | Ramp from zero | Downtime loss + post-conversion ramp from reduced baseline |
| Residual value | N/A (no prior assets) | Old-format equipment salvage |

### Missing vs. Zero Semantics

- **`null`** = unknown, not measured, not yet provided. Consumers MUST NOT substitute zero or any greenfield/system default. Appears on `residual_value`, `disposal_cost`, `ramp_months`, `ramp_curve_id`, and `daily_baseline_revenue`.
- **`0`** = explicitly measured and confirmed to be zero. Legitimate for `residual_value` (e.g., fully depreciated equipment) and `downtime_days` (e.g., overnight swap).

## Source Inventory Summary

Inspected at `origin/dev` tip `cf04c046` (2026-09-08T16:32Z):

| Source Path | What Exists | What Is Missing for FORMAT_CONVERSION |
|-------------|-------------|--------------------------------------|
| `infra/db/migrations/000001_baseline_canonical_schema.sql` | `core.stores.store_format_code` (static column) | No `core.store_format_conversions` table |
| `infra/db/migrations/000002_data_domain_canonical_entities.sql` | `core.stores.store_format_code` (static column) | No conversion event table |
| `infra/db/migrations/000004_durable_product_domain.sql` | `stores.store_format_code` (static column, SQLite) | No conversion event table |
| `infra/db/migrations/000001`–`000023` (all 27 SQL migrations) | Static format code in 000001, 000002, 000004 | Zero conversion event tables across all migrations |
| `modules/site_economics/domain/formats.py` | `TargetFormatRegistry` (ODAY_G2, G3_COMPACT, FLAGSHIP) | No conversion matrix or transition rules |
| `modules/site_economics/domain/simulator.py` | `SimulationInput` — greenfield only | No `ConversionSimulationInput`, no downtime/residual model |
| `modules/sitescore/domain/scoring.py` | SiteScore feature input | No format conversion impact fields |
| `delivery_toolchain/governance/set_valued_requirements.json` | FORMAT_CONVERSION status: `absent`, disposition: `BLOCKED_BY_EVIDENCE` | As expected for Stage A |

> [!NOTE]
> The inspection command `rg -n "FORMAT_CONVERSION|format_conversion|Brownfield|brownfield" modules models solver pipelines` returned **zero matches (exit code 1)** across the 4 inspected production directories (`modules/`, `models/`, `solver/`, `pipelines/`), confirming absence within inspected production code paths as documented in `ODP_SITE001_DATA_READINESS_2026-09-03.md`. Scope is restricted to the inspected directories and does not assert absence in unsearched areas without separate inspection.
