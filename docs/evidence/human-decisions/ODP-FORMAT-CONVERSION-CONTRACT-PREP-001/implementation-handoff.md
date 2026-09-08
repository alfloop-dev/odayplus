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
| 1 | H04 response provides real conversion events (§2.1) | ⏳ Awaiting | Store Operations Lead |
| 2 | H04 response provides financial parameters (§2.3) | ⏳ Awaiting | Finance / Real Estate Lead |
| 3 | H04 response confirms data source and responsible owner (§2.4) | ⏳ Awaiting | Store Operations Lead |
| 4 | Cross-source/consumer contract review completed | ⏳ After H04 | Engineering |
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
- [ ] Handle null values per field dictionary (null ≠ zero)
- [ ] Apply conversion-specific ramp curve if different from greenfield

### 4.3 Event Processing

- [ ] Implement idempotent event write (keyed on `event_id` and `idempotency_key`)
- [ ] Implement replay without duplicate cost entries
- [ ] Validate `from_format_code ≠ to_format_code`
- [ ] Validate both format codes exist in `TargetFormatRegistry`

### 4.4 Governance

- [ ] Update `set_valued_requirements.json`: `FORMAT_CONVERSION` status `absent` → `satisfied`
- [ ] Update disposition `BLOCKED_BY_EVIDENCE` → `VERIFIED`
- [ ] Ensure `check_requirement_members.py` passes
- [ ] Remove/archive `HB-SITE001-FORMAT-CONVERSION-001` handback package

### 4.5 Acceptance Tests (per ODP_SITE001_COMPONENT_DISPOSITIONS §4.2)

- [ ] **Test 1 (Downtime verification)**: Same area/format, brownfield year-1 NCF reflects downtime loss; payback longer than zero-downtime control
- [ ] **Test 2 (Residual value verification)**: Higher residual value → proportionally lower net capex
- [ ] **Test 3 (Replay idempotency)**: Same event_id → no duplicate cost entries
- [ ] **Test 4 (Missing data)**: Null financial fields → unquantified flag, not zero substitution

## 5. Source Inventory Receipts

### 5.1 Search Command

```
Command: rg -n "FORMAT_CONVERSION|format_conversion|Brownfield|brownfield" modules models solver pipelines
Head: 9048161e
UTC: 2026-09-08T15:22:47Z
Exit code: 1 (no matches in specified directories)
Result: Zero hits in production code paths — confirms repo-wide absence
```

### 5.2 Governance State

```
File: delivery_toolchain/governance/set_valued_requirements.json
Member: FORMAT_CONVERSION
status: absent
disposition.state: BLOCKED_BY_EVIDENCE
disposition.evidence_owner: Retail Operations Lead / Site Economics Lead
disposition.next_review_date: 2026-10-01
```

### 5.3 Codebase Evidence Summary

| Path | What Exists | Conversion Gap |
|------|-------------|---------------|
| `infra/db/migrations/000001_baseline_canonical_schema.sql` | `core.stores.store_format_code` static column | No `store_format_conversions` table |
| `infra/db/migrations/000004_durable_product_domain.sql` | `stores.store_format_code` static column (SQLite) | No conversion event table |
| `modules/site_economics/domain/formats.py` | `TargetFormatRegistry` — 3 formats (G2, G3_COMPACT, FLAGSHIP) | No conversion matrix or transition rules |
| `modules/site_economics/domain/simulator.py` | `SimulationInput` — greenfield capex/cash flow only | No `ConversionSimulationInput` or downtime/residual model |
| `modules/sitescore/domain/scoring.py` | `SiteScoreFeatureInput` — no conversion fields | SiteScore does not consume conversion data |
| `shared/domain/models.py` | `store_format_code: str = ""` | Static field, no history |

## 6. Coordination Notes

- **WP-30 (BRAND_TRANSFER)**: Shares scoring files in `modules/sitescore/`. If both Stage B tasks run concurrently, serialize changes to shared files per execution plan §6 WP-31.
- **WP-90 (Integration)**: Stage B completion feeds into WP-90 for final governance manifest update and structural closeout.
- **Governance checker**: `check_requirement_members.py` must pass in both Stage A (BLOCKED) and Stage B (VERIFIED) states. Do not break the checker during transition.
