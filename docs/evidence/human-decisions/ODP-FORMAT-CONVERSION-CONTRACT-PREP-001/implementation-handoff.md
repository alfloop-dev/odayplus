# Implementation Handoff — FORMAT_CONVERSION Contract Prep → Stage B

- **Task**: `ODP-FORMAT-CONVERSION-CONTRACT-PREP-001`
- **Work Package**: WP-31 (§6 of execution plan)
- **Stage**: A (engineering preparation) → handoff to B (real data integration)
- **Date**: 2026-09-08
- **Owner**: Antigravity
- **Reviewer**: Codex2

---

## 1. What This Stage Delivered

| Artifact | Description | Status |
|----------|-------------|--------|
| [event-contract-draft.json](event-contract-draft.json) | JSON Schema for `FORMAT_CONVERSION` events with 12 required + 13 optional fields | Draft complete |
| [field-dictionary.md](field-dictionary.md) | Per-field documentation: type, source, owner, design rationale, inspected evidence | Draft complete |
| [source-consumer-map.json](source-consumer-map.json) | 4 sources × 4 consumers with current status and Stage B actions | Draft complete |
| [human-input-request-H04.md](human-input-request-H04.md) | Detailed H04 request: real events, financial parameters, data sources, acceptance tests | Awaiting response |
| [implementation-handoff.md](implementation-handoff.md) | This document | Complete |

## 2. What This Stage Did NOT Do

> [!CAUTION]
> The following are explicitly **not done** and **not claimed**:

- ❌ No production producer exists for FORMAT_CONVERSION events
- ❌ No database migration (`core.store_format_conversions`) was created
- ❌ No `ConversionSimulationInput` was added to `simulator.py`
- ❌ No brownfield financial model was implemented
- ❌ No real conversion events or financial parameters were obtained
- ❌ No runtime, provider, or security gate was verified
- ❌ No waiver or exception was created
- ❌ `FORMAT_CONVERSION` disposition remains `BLOCKED_BY_EVIDENCE` — not changed

## 3. Stage B Entry Conditions

Stage B (`WP-31B`) cannot begin until **all** of the following are met:

| # | Condition | Status | Blocker |
|---|-----------|--------|---------|
| 1 | H04 response provides real conversion events or planned-event specifications with acquisition owner and timing (§2.1) | ⏳ Awaiting | Store Operations Lead |
| 2 | H04 response provides financial parameters & downtime rules (§2.3) | ⏳ Awaiting | Finance / Real Estate Lead |
| 3 | H04 response confirms data source and responsible owner (§2.4) | ⏳ Awaiting | Store Operations Lead |
| 4 | Cross-source/consumer contract review completed (event schema, persistence migration design, simulator input) | ⏳ After H04 | Engineering |
| 5 | Event schema finalized (draft → v1.0.0) | ⏳ After H04 | Engineering |

## 4. Stage B Implementation Checklist

When entry conditions are met, the Stage B implementer must:

### 4.1 Persistence Layer

- [ ] Create PostgreSQL migration for `core.store_format_conversions` per [ODP_SITE001_COMPONENT_DISPOSITIONS §4.2](../../ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md) spec
- [ ] Create corresponding SQLite migration for `000004` alignment
- [ ] Add FK constraint to `core.stores(store_id)`
- [ ] Add unique constraint on `event_id`

### 4.2 Domain Model

- [ ] Define `ConversionSimulationInput` dataclass in `modules/site_economics/domain/simulator.py`
- [ ] Implement downtime loss calculation: `downtime_loss = daily_baseline_revenue × downtime_days`
- [ ] Implement net capex: `net_capex = remodeling_capex - residual_value + disposal_cost`
- [ ] Handle null values per field dictionary (null ≠ zero; missing curves stay unquantified)
- [ ] Apply conversion-specific ramp curve if different from greenfield; if unknown, do not default to greenfield

### 4.3 Event Processing

- [ ] Implement idempotent event write (keyed on `event_id` and `idempotency_key`)
- [ ] Implement replay without duplicate cost entries
- [ ] Validate `from_format_code ≠ to_format_code`
- [ ] Validate both format codes exist in `TargetFormatRegistry`

### 4.4 Governance Handoff (Scoped for WP-90 Centralized Integration)

Per Execution Plan §5 & §6 (WP-00 / WP-90), centralized governance manifests (`set_valued_requirements.json`, `docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md`) are updated centrally by **WP-90** to prevent multi-worker write contention.

Stage B implementer responsibility:
- [ ] Deliver verified implementation evidence, test receipts (exit code 0, exact HEAD SHA, duration), and migration verification records
- [ ] Deliver scoped handoff summary confirming `FORMAT_CONVERSION` members are satisfied and verified by automated tests
- [ ] Hand over verified package to WP-90 for centralized governance update (`absent` → `satisfied`, `BLOCKED_BY_EVIDENCE` → `VERIFIED`) and handback archival (`HB-SITE001-FORMAT-CONVERSION-001`)
- [ ] Stage B does NOT directly mutate centralized governance manifests

### 4.5 Acceptance Tests (per ODP_SITE001_COMPONENT_DISPOSITIONS §4.2)

- [ ] **Test 1 (Downtime verification)**: Same area/format, brownfield year-1 NCF reflects downtime loss; payback longer than zero-downtime control
- [ ] **Test 2 (Residual value verification)**: Higher residual value → proportionally lower net capex
- [ ] **Test 3 (Replay idempotency)**: Same event_id → no duplicate cost entries
- [ ] **Test 4 (Missing data)**: Null financial fields → unquantified flag, not zero substitution

## 5. Source Inventory Receipts

### 5.1 Keyword Search in Production Paths

```
Command: rg -n "FORMAT_CONVERSION|format_conversion|Brownfield|brownfield" modules models solver pipelines
Head: cf04c046
UTC: 2026-09-08T16:32:59Z
Exit code: 1 (no matches in specified directories)
Scope: 4 inspected production directories (modules/, models/, solver/, pipelines/).
Result: Zero matches in inspected production paths. Scope is restricted to these 4 directories and does not assert absence across uninspected areas without separate evidence.
```

### 5.2 Migration Files Inspection (000001–000023)

```
Command 1: grep -n -i "format_conversion\|store_format_conversions" infra/db/migrations/*.sql
Head: cf04c046
UTC: 2026-09-08T16:32:34Z
Exit code: 1 (0 matches across all 27 SQL migration files 000001–000023)

Command 2: grep -n "store_format_code" infra/db/migrations/*.sql
Head: cf04c046
UTC: 2026-09-08T16:32:35Z
Exit code: 0
Output:
  infra/db/migrations/000001_baseline_canonical_schema.sql:83:    store_format_code VARCHAR(100),
  infra/db/migrations/000002_data_domain_canonical_entities.sql:83:    store_format_code VARCHAR(100),
  infra/db/migrations/000004_durable_product_domain.sql:67:    store_format_code TEXT,
Result: Static column only in 000001, 000002, 000004; zero conversion event or transition tables exist across 000001–000023.
```

### 5.3 Domain & Simulator Inspection

```
Command: python3 -c "from modules.site_economics.domain.formats import DEFAULT_FORMAT_REGISTRY; print(DEFAULT_FORMAT_REGISTRY.list_codes())"
Head: cf04c046
UTC: 2026-09-08T16:33:01Z
Exit code: 0
Output: ['ODAY_FLAGSHIP', 'ODAY_G2', 'ODAY_G3_COMPACT']
Result: TargetFormatRegistry provides new-store format selection by area. No conversion matrix or transition rules exist.

File inspection: modules/site_economics/domain/simulator.py
Head: cf04c046
Result: SimulationInput models greenfield new-store capex/cash flow only; no ConversionSimulationInput or downtime/residual/conversion-ramp logic exists.

File inspection: modules/sitescore/domain/scoring.py
Head: cf04c046
Result: SiteScoreFeatureInput contains no format conversion fields; SiteScore currently scores greenfield candidates only.
```

### 5.4 Governance State

```
Command: python3 delivery_toolchain/governance/check_requirement_members.py --show-dispositions
Head: cf04c046
UTC: 2026-09-08T16:33:03Z
Exit code: 0
Output snippet:
  State BLOCKED_BY_EVIDENCE (4):
    - ODP-FR-SITE-001::FORMAT_CONVERSION
    ...
File: delivery_toolchain/governance/set_valued_requirements.json
Member: FORMAT_CONVERSION
status: absent
disposition.state: BLOCKED_BY_EVIDENCE
disposition.evidence_owner: Retail Operations Lead / Site Economics Lead
disposition.next_review_date: 2026-10-01
disposition.formal_handback_ref: docs/evidence/ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md#33-人類授權移交單human-authority-handback-package
```

### 5.5 Codebase Evidence Summary

| Path | What Exists | Conversion Gap |
|------|-------------|---------------|
| `infra/db/migrations/000001_baseline_canonical_schema.sql` | `core.stores.store_format_code` static column | No `store_format_conversions` table |
| `infra/db/migrations/000002_data_domain_canonical_entities.sql` | `core.stores.store_format_code` static column | No `store_format_conversions` table |
| `infra/db/migrations/000004_durable_product_domain.sql` | `stores.store_format_code` static column (SQLite) | No conversion event table |
| `infra/db/migrations/000001`–`000023` (all 27 SQL migrations) | Static column only in 000001, 000002, 000004 | Zero conversion event tables across all migrations |
| `modules/site_economics/domain/formats.py` | `TargetFormatRegistry` — 3 formats (G2, G3_COMPACT, FLAGSHIP) | No conversion matrix or transition rules |
| `modules/site_economics/domain/simulator.py` | `SimulationInput` — greenfield capex/cash flow only | No `ConversionSimulationInput` or downtime/residual model |
| `modules/sitescore/domain/scoring.py` | `SiteScoreFeatureInput` — no conversion fields | SiteScore does not consume conversion data |
| `shared/domain/models.py` | `store_format_code: str = ""` | Static field, no history |

## 6. Coordination Notes

- **WP-30 (BRAND_TRANSFER)**: Shares scoring files in `modules/sitescore/`. If both Stage B tasks run concurrently, serialize changes to shared files per execution plan §6 WP-31.
- **WP-90 (Integration & Structural Closeout)**: Stage B completion delivers verified evidence and scoped handoff to WP-90 for centralized requirement disposition updates (`absent` → `satisfied`, `BLOCKED_BY_EVIDENCE` → `VERIFIED`) and formal handback archival per execution plan §5 and §6 WP-90.
- **Governance checker**: `check_requirement_members.py` must pass in both Stage A (BLOCKED) and Stage B (VERIFIED) states. Do not break the checker during transition.
