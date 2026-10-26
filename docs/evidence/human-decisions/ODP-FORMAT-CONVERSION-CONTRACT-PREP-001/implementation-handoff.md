# Implementation Handoff — FORMAT_CONVERSION Contract Prep → Stage B

- **Task**: `ODP-FORMAT-CONVERSION-CONTRACT-PREP-001`
- **Work Package**: WP-31 (§6 of execution plan)
- **Stage**: A (engineering preparation) → handoff to B (real data integration)
- **Date**: 2026-09-08
- **Owner**: Claude
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

- [ ] Implement idempotent event write keyed on `event_id` (primary identity, always applied)
- [ ] Apply `idempotency_key` as a secondary dedup key **only** when it is a valid non-null, non-empty string; reject empty strings at validation
- [ ] Guarantee that events with `null`/omitted `idempotency_key` are never collapsed with each other — they dedup on `event_id` alone
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

- [ ] **Test 1 (Downtime verification, controlled)**: Take one conversion scenario and hold every other financial input fixed (`remodeling_capex`, `residual_value`, `disposal_cost`, `ramp_months`, `ramp_curve_id`, `daily_baseline_revenue`, target format, area); vary **only** `downtime_days`. Higher `downtime_days` MUST produce a strictly lower year-1 net cash flow and a payback period that is no shorter. Do **not** assert that a brownfield conversion is worse than a greenfield scenario — different capex/residual/ramp inputs can legitimately outweigh downtime, and such an assertion would reject correct conversion economics
- [ ] **Test 2 (Residual value verification)**: Higher residual value → proportionally lower net capex
- [ ] **Test 3 (Replay idempotency)**: Same `event_id` → no duplicate cost entries
- [ ] **Test 3b (Secondary key, distinct events preserved)**: Two legitimate distinct events (different `event_id`) that both carry `idempotency_key: null`, and a second pair that both omit the field, MUST remain two separate events with both cost entries retained. Only two events sharing the same valid non-null key may collapse; an empty-string key is a validation error, not a dedup participant
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

### 5.2 Migration Tree Inspection (full tree)

The earlier receipt used the glob `infra/db/migrations/*.sql`, which reaches only the 27 top-level SQL
files. That glob does **not** reach `infra/db/migrations/assisted_listing_intake/*.sql` or the Alembic
`infra/db/migrations/versions/*.py` migrations, so it could not support any claim about "all migrations".
The inspection was therefore re-run recursively over the whole migrations tree. The absence claim below
now rests on the recursive receipts, not on the top-level glob.

```
Command 1: grep -rn -i -e format_conversion -e store_format_conversions infra/db/migrations
Head: 944f4d719b29e765605f55a5fbc366aca3430c36
UTC: 2026-09-08T17:56:54Z
Raw exit code: 1
Output: (empty — zero matches anywhere in the migrations tree)

Command 2: grep -rn store_format_code infra/db/migrations
Head: 944f4d719b29e765605f55a5fbc366aca3430c36
UTC: 2026-09-08T17:56:54Z
Raw exit code: 0
Output:
  infra/db/migrations/000004_durable_product_domain.sql:67:    store_format_code TEXT,
  infra/db/migrations/000001_baseline_canonical_schema.sql:83:    store_format_code VARCHAR(100),
  infra/db/migrations/000002_data_domain_canonical_entities.sql:83:    store_format_code VARCHAR(100),

Command 3: ls -1 infra/db/migrations/*.sql | wc -l
Head: 944f4d719b29e765605f55a5fbc366aca3430c36
UTC: 2026-09-08T17:57:12Z
Raw exit code: 0
Output: 27

Command 4: find infra/db/migrations -mindepth 2 -name "*.sql" | wc -l
Head: 944f4d719b29e765605f55a5fbc366aca3430c36
UTC: 2026-09-08T17:57:12Z
Raw exit code: 0
Output: 6

Command 5: find infra/db/migrations/versions -name "*.py" | wc -l
Head: 944f4d719b29e765605f55a5fbc366aca3430c36
UTC: 2026-09-08T17:57:12Z
Raw exit code: 0
Output: 17

Command 6: grep -rn -i -e store_format_conversions infra shared modules
Head: 944f4d719b29e765605f55a5fbc366aca3430c36
UTC: 2026-09-08T17:57:28Z
Raw exit code: 1
Output: (empty — zero matches)
```

Inspected set: **50 migration files** — 27 top-level SQL + 6 `assisted_listing_intake/*.sql` +
17 Alembic `versions/*.py`.
Result: the static `store_format_code` column appears only in `000001`, `000002` and `000004`. No
`store_format_conversions` table, conversion event table or transition table exists anywhere in the
migrations tree, and no such identifier exists under `infra/`, `shared/` or `modules/`.

### 5.3 Domain, Simulator & Scoring Inspection

Every observation in this section carries its own command, UTC and raw exit code. The previous version
of this section asserted results for `simulator.py` and `scoring.py` without recording the read command
that produced them; those assertions have been re-derived from the receipts below, and two of them were
**too absolute and are corrected here**.

All commands ran in this task worktree at HEAD `944f4d719b29e765605f55a5fbc366aca3430c36`. This task's
commits touch only files under `docs/evidence/human-decisions/ODP-FORMAT-CONVERSION-CONTRACT-PREP-001/`,
so the inspected source paths are byte-identical at the delivery HEAD. Per-file blob SHAs are recorded so
each observation stays verifiable independently of the commit graph.

```
Command 0 (blob identity): git rev-parse HEAD:modules/site_economics/domain/simulator.py HEAD:modules/site_economics/domain/formats.py HEAD:modules/sitescore/domain/scoring.py HEAD:shared/domain/models.py
Head: 944f4d719b29e765605f55a5fbc366aca3430c36
UTC: 2026-09-08T17:57:12Z
Raw exit code: 0
Output:
  modules/site_economics/domain/simulator.py  30844af007ce25db7ed528521920293aec167416
  modules/site_economics/domain/formats.py    61196d515612c330b44d62c916636aeddf6be674
  modules/sitescore/domain/scoring.py         1f8f50ab812abe91d0444e230fd06f94717a93dc
  shared/domain/models.py                     5952c75dd3e83541a6ce4478c096e85be2b41334
```

**Observation A — format registry exposes selection only, no transition logic.**

```
Command A1: python3 -c "from modules.site_economics.domain.formats import DEFAULT_FORMAT_REGISTRY; print(DEFAULT_FORMAT_REGISTRY.list_codes())"
Head: cf04c046
UTC: 2026-09-08T16:33:01Z
Raw exit code: 0
Output: ['ODAY_FLAGSHIP', 'ODAY_G2', 'ODAY_G3_COMPACT']

Command A2: grep -n -E "^(class |    def |def )" modules/site_economics/domain/formats.py
Head: 944f4d719b29e765605f55a5fbc366aca3430c36 (blob 61196d51)
UTC: 2026-09-08T17:57:12Z
Raw exit code: 0
Output (public surface):
  171:class TargetFormatSpec:
  400:class TargetFormatRegistry:
  413:    def register(...)   419:    def get(...)   430:    def list_formats(...)
  433:    def list_codes(...) 436:    def find_best_format_for_area(area_ping)

Command A3: grep -n -i -E "transition|conversion|from_format|to_format|matrix" modules/site_economics/domain/formats.py
Head: 944f4d719b29e765605f55a5fbc366aca3430c36 (blob 61196d51)
UTC: 2026-09-08T17:57:12Z
Raw exit code: 1
Output: (empty — zero matches)
```

Result: A1 proves which codes are registered. A2 and A3 are what establish the stronger claim — the whole
declared surface of `formats.py` is registration plus `find_best_format_for_area()` area-based selection,
and the file contains no transition, conversion or from/to-format identifier at all. No conversion matrix
or transition rule exists.

**Observation B — simulator models greenfield only. (Corrected: residual value IS modelled.)**

```
Command B1: grep -n -E "^(class |    def |def )" modules/site_economics/domain/simulator.py
Head: 944f4d719b29e765605f55a5fbc366aca3430c36 (blob 30844af0)
UTC: 2026-09-08T17:57:12Z
Raw exit code: 0
Output (public surface): 29:class SimulationInput  89:class SimulationResult
  269:class SiteEconomicsSimulator  272:    def simulate(sim_input: SimulationInput)
  100:def compute_pmt  111:def compute_npv  122:def compute_irr  193:def compute_payback

Command B2: grep -n -i -E "downtime|conversion|brownfield|remodel" modules/site_economics/domain/simulator.py
Head: 944f4d719b29e765605f55a5fbc366aca3430c36 (blob 30844af0)
UTC: 2026-09-08T17:57:12Z
Raw exit code: 1
Output: (empty — zero matches)

Command B3: grep -n -i -E "residual|disposal" modules/site_economics/domain/simulator.py
Head: 944f4d719b29e765605f55a5fbc366aca3430c36 (blob 30844af0)
UTC: 2026-09-08T17:57:12Z
Raw exit code: 0
Output: 325, 335, 336, 351, 354, 529, 531 (equipment residual/salvage, lease-deposit recovery,
        decommissioning cost — all reached through format_spec.machine_mix / format_spec.residual_spec)
```

Result and correction: the only entry point is `simulate(SimulationInput)`; there is no
`ConversionSimulationInput`, and B2 shows zero downtime, conversion, brownfield or remodel logic.
However, the earlier wording "no downtime/residual/conversion-ramp logic exists" was **wrong about
residual value**: B3 shows `simulator.py` already models equipment residual value and salvage for
NEW-store assets via `format_spec.residual_spec` / `machine_mix`. What is missing is the *brownfield*
use of residual value — salvaging the OLD format's assets on conversion — not residual modelling as
such. Stage B should extend the existing residual path rather than assume none exists.

**Observation C — SiteScore. (Corrected: a format field exists, but no conversion field.)**

```
Command C1: grep -n -i -E "conversion|brownfield|from_format|downtime|remodel|brand_transfer" modules/sitescore/domain/scoring.py
Head: 944f4d719b29e765605f55a5fbc366aca3430c36 (blob 1f8f50ab)
UTC: 2026-09-08T17:57:28Z
Raw exit code: 1
Output: (empty — zero matches)

Command C2: sed -n '60,75p' modules/sitescore/domain/scoring.py
Head: 944f4d719b29e765605f55a5fbc366aca3430c36 (blob 1f8f50ab)
UTC: 2026-09-08T17:57:28Z
Raw exit code: 0
Output: class SiteScoreFeatureInput — candidate_site_id, tenant_id,
        target_format_code: str = "ODAY_G2" (L70), feature_snapshot_time, view_version,
        heat_zone_id, h3_index, latitude, ...
```

Result and correction: the earlier wording "`SiteScoreFeatureInput` contains no format conversion fields"
was ambiguous. C2 shows the input **does** carry `target_format_code` (L70) — the greenfield target format
for a candidate site. C1 shows it carries no conversion, from-format, downtime or brand-transfer field.
Precise claim: SiteScore scores greenfield candidates against a single target format and consumes no
conversion data.

**Observation D — canonical store model carries a static format code with no history.**

```
Command D1: grep -n "store_format_code" shared/domain/models.py
Head: 944f4d719b29e765605f55a5fbc366aca3430c36 (blob 5952c75d)
UTC: 2026-09-08T17:57:12Z
Raw exit code: 0
Output: 64:    store_format_code: str = ""

Command D2: sed -n '58,70p' shared/domain/models.py
Head: 944f4d719b29e765605f55a5fbc366aca3430c36 (blob 5952c75d)
UTC: 2026-09-08T17:57:28Z
Raw exit code: 0
Output: tenant_id, brand_id, source_store_id, store_name,
        store_status ("planned/open/suspended/closed/transferred"), ownership_type,
        store_format_code: str = "" (L64), opened_on, closed_on, address_id, region_code, ...
```

Result: `store_format_code` is a single scalar field at L64 with a `""` default and no accompanying
history, effective-date or previous-format field. The store model records only the current format.

**Observation E — production keyword scan re-confirmed at the delivery HEAD.**

```
Command E1: grep -rn -E "FORMAT_CONVERSION|format_conversion|Brownfield|brownfield" modules models solver pipelines
Head: 944f4d719b29e765605f55a5fbc366aca3430c36
UTC: 2026-09-08T17:58:48Z
Raw exit code: 1
Output: (empty — zero matches)
```

Result: reproduces the §5.1 `rg` receipt taken at `cf04c046`, now at the delivery HEAD with a different
tool. Scope remains the 4 named directories and asserts nothing about unsearched areas.

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
| `infra/db/migrations/` — full tree, 50 files (27 top-level SQL + 6 `assisted_listing_intake/*.sql` + 17 Alembic `versions/*.py`) | Static column only in 000001, 000002, 000004 | Zero conversion event tables anywhere in the tree (§5.2 Command 1, raw exit 1) |
| `modules/site_economics/domain/formats.py` | `TargetFormatRegistry` — 3 formats (G2, G3_COMPACT, FLAGSHIP) | No conversion matrix or transition rules |
| `modules/site_economics/domain/simulator.py` | `SimulationInput` — greenfield capex/cash flow; **does** model equipment residual/salvage for new-store assets (§5.3 B3) | No `ConversionSimulationInput`; zero downtime/conversion/brownfield/remodel logic (§5.3 B2, raw exit 1). Residual exists but has no brownfield old-asset path |
| `modules/sitescore/domain/scoring.py` | `SiteScoreFeatureInput.target_format_code` (L70) — greenfield target format | No conversion/from-format/downtime field (§5.3 C1, raw exit 1); SiteScore does not consume conversion data |
| `shared/domain/models.py` | `store_format_code: str = ""` at L64 (§5.3 D1, raw exit 0) | Scalar field only — no format history, effective-date or previous-format field (§5.3 D2) |

## 6. Coordination Notes

- **WP-30 (BRAND_TRANSFER)**: Shares scoring files in `modules/sitescore/`. If both Stage B tasks run concurrently, serialize changes to shared files per execution plan §6 WP-31.
- **WP-90 (Integration & Structural Closeout)**: Stage B completion delivers verified evidence and scoped handoff to WP-90 for centralized requirement disposition updates (`absent` → `satisfied`, `BLOCKED_BY_EVIDENCE` → `VERIFIED`) and formal handback archival per execution plan §5 and §6 WP-90.
- **Governance checker**: `check_requirement_members.py` must pass in both Stage A (BLOCKED) and Stage B (VERIFIED) states. Do not break the checker during transition.

## 7. Review Findings Log (Stage A)

Findings raised by reviewer Codex2 against reviewed HEAD `944f4d71`, and their disposition in this
revision. All paths are relative to
`docs/evidence/human-decisions/ODP-FORMAT-CONVERSION-CONTRACT-PREP-001/`.

| ID | Finding | Disposition |
|----|---------|-------------|
| R3 | Simulator/scoring inspections claimed without their read command, UTC or raw exit code; `shared/domain/models.py` observation had no receipt; the "absent from any migration" claim rested on an `infra/db/migrations/*.sql` glob that reached only the 27 top-level SQL files | Addressed. §5.2 replaced with a recursive scan of the full 50-file migrations tree (27 top-level SQL + 6 `assisted_listing_intake/*.sql` + 17 Alembic `versions/*.py`), raw exit 1, so the tree-wide claim is now supported rather than narrowed. §5.3 rewritten as Observations A–E, each with exact argv, UTC, raw exit code and per-file blob SHA; `shared/domain/models.py` is Observation D. Two claims were found **over-broad during re-inspection and corrected**: `simulator.py` does already model equipment residual/salvage for new-store assets (§5.3 B3), and `SiteScoreFeatureInput` does carry `target_format_code` (§5.3 C2). Corrections propagated to `source-consumer-map.json`, `field-dictionary.md` and `README.md` |
| R6 | `source-consumer-map.json` required brownfield first-year cash flow to be worse than a greenfield zero-downtime baseline, which can reject economically correct conversions when capex/residual/ramp differ | Addressed. The acceptance case is now a single-variable controlled comparison: same conversion, every other financial input held fixed, only `downtime_days` varied; higher downtime must strictly lower year-1 NCF and must not shorten payback. The cross-scenario brownfield-vs-greenfield assertion is explicitly rejected, with the reason recorded. Aligned in `source-consumer-map.json`, `human-input-request-H04.md` §3.1 and §4.5 Test 1 above |
| R7 | `event-contract-draft.json` permitted `idempotency_key: null` while mandating deduplication whenever the field was present and equal, so two distinct events carrying null would collapse and lose costs | Addressed. Only a supplied valid non-null, non-empty key participates in secondary deduplication; `null`/omitted keys dedup on `event_id` alone and never collapse with each other; empty string is a validation error (`minLength: 1`) and is not normalised to null. Aligned across `event-contract-draft.json` (field description and `_design_notes.replay_idempotency`), `field-dictionary.md` rows 1 and 24, `human-input-request-H04.md` §3.4, and §4.3 / §4.5 Test 3b above. The planned 31B case preserving distinct events with null/omitted secondary keys is recorded for Stage B; no production implementation is performed in Stage A |

R1, R2, R4 and R5 from the earlier review round remain addressed and were not modified by this revision.
D17 (implement rather than waive) is preserved. H04, Stage B and production continue to wait on real
data; this revision claims no runtime, provider or security gate pass.
