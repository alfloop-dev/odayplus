---
doc_id: ODP-EMGI-INDEX-003
title: ODay Plus EMGI v0.3.0 Design Package Index and LLM Handoff
version: 0.3.0
status: approved-for-dispatch
language: zh-TW
owner: Product / Market Intelligence / System Design
target_repository: alfloop-dev/odayplus
target_branch: dev
reviewed_baseline_sha: 0d1603cf347e30c9cf2f25f0eecc10673ac55015
updated_at: 2026-08-13
---

# ODay Plus EMGI v0.3.0 Design Package Index and LLM Handoff

## 1. Purpose

This package is the binding implementation handoff for **External Market & Geographic Intelligence (EMGI)** in ODay Plus.

It consolidates the previously proposed product definition, source-feasibility blueprint, the broader ODay Plus specification, current repository inspection, official source verification, and open-source project review into a contract stack that Codex, Claude, Antigravity, and other workers can implement in parallel.

It does **not** claim that the current HeatZone, SiteScore, market-intelligence data products, external providers, source coverage, or investment recommendations are production-ready.

## 2. Binding Order

When documents conflict, apply:

1. `docs/design/emgi/v0.3/PRODUCT_01_SCOPE_PRINCIPLES.md`
2. `docs/design/emgi/v0.3/PRODUCT_02_USERS_SPACE_SCOPE.md`
3. `docs/design/emgi/v0.3/PRODUCT_03_DOMAINS_DATA_PRODUCTS.md`
4. `docs/design/emgi/v0.3/PRODUCT_04_API_GOVERNANCE_ADRS.md`
5. `docs/design/emgi/v0.3/SYSTEM_01_BASELINE_ARCHITECTURE_CONTRACTS.md`
6. `docs/design/emgi/v0.3/SYSTEM_02_SOURCES_DOMAINS_MATERIALIZATION.md`
7. `docs/design/emgi/v0.3/SYSTEM_03_EXECUTION_RELEASE.md`
8. `docs/design/emgi/v0.3/SOURCE_01_DATA_SOURCES.md`
9. `docs/design/emgi/v0.3/SOURCE_02_OSS_PRIORITIES_RECEIPTS.md`
10. `docs/design/emgi/v0.3/REVIEW_MANIFEST.yaml`
11. `docs/design/emgi/v0.3/RELATION_OWNERSHIP_BASELINE.yaml`
12. `docs/design/emgi/v0.3/tasks/manifest.json`
13. `docs/design/emgi/v0.3/EXECUTION_01_RULES_AND_INVENTORY.md`

The task manifest is authoritative for all 45 task IDs and dependency edges. `kernel-a.json`, `kernel-b.json`, and `safety.json` additionally define detailed owned paths, contracts, acceptance clauses, and verification commands for the 13 highest-risk contract/safety tasks. `EXECUTION_01_RULES_AND_INVENTORY.md` is the binding responsibility and verification authority for the remaining 32 tasks.

## 3. Checked-in Files

```text
docs/design/ODAY_PLUS_EMGI_DESIGN_PACKAGE_INDEX_2026-08-13.md
docs/design/emgi/v0.3/
  PRODUCT_01_SCOPE_PRINCIPLES.md
  PRODUCT_02_USERS_SPACE_SCOPE.md
  PRODUCT_03_DOMAINS_DATA_PRODUCTS.md
  PRODUCT_04_API_GOVERNANCE_ADRS.md
  SYSTEM_01_BASELINE_ARCHITECTURE_CONTRACTS.md
  SYSTEM_02_SOURCES_DOMAINS_MATERIALIZATION.md
  SYSTEM_03_EXECUTION_RELEASE.md
  SOURCE_01_DATA_SOURCES.md
  SOURCE_02_OSS_PRIORITIES_RECEIPTS.md
  EXECUTION_01_RULES_AND_INVENTORY.md
  REVIEW_MANIFEST.yaml
  RELATION_OWNERSHIP_BASELINE.yaml
  tasks/
    manifest.json
    kernel-a.json
    kernel-b.json
    safety.json
```

## 4. Superseded Assumptions

- 12-week, Sprint 0–5, sequential PR scheduling.
- One scope enum mixing ownership, sharing, sensitivity, and purpose.
- Fake tenant ownership for national public data.
- Provider registry rows as proof of live datasets.
- Missing values becoming zero or confidence one.
- Same-H3 counts named as 500-meter or travel-time features.
- Table names used as source snapshot IDs.
- Platform ingestion success treated as per-tenant/per-store coverage.
- UTC used to cut Taiwan business days.
- One relation name used for current candidate scoring and opened-store training.
- Model gates that ignore downstream rent, CAPEX, margin, feasibility, and cannibalization defaults.
- HeatZone or SiteScore heuristics treated as binding production decisions.
- Purchasing nationwide paid data before baseline and incremental-value evidence.
- Blocking API, UI, domain, data-product, and test work on live-source completion.

## 5. Non-Negotiable Rules

1. Start from exact reviewed `dev` SHA or a newer fetched `dev`; never stale `main`.
2. Every task targets `dev`; owner and reviewer differ.
3. Central registry, aggregate OpenAPI, contract lock, relation ownership, and ordered migration surfaces are generated or assembled only by designated integration tasks.
4. Missing, stale, partial, unlicensed, unauthorized, quarantined, source-error, saturated, and truncated states never silently become zero.
5. Zero requires negative evidence and proven search/partition completeness.
6. Business time, effective time, knowledge time, feature time, label time, and build time are separate.
7. Feature manifests never contain future label evidence.
8. A model score may exist while the decision is not ready.
9. Physical feasibility and economics fail closed before binding GO/WAIT/REJECT.
10. Existing HeatZone and SiteScore heuristics remain shadow-only until v3 gates pass.
11. Technical readiness and policy readiness are separate; policy warnings do not block technical research or shadow evaluation.
12. Discovery sources publish observations, not unquestioned truth.
13. Source disagreement and upstream dependency are preserved.
14. Cross-brand data is analytical-only unless explicitly granted.
15. Runtime completion requires durable persistence, restart readback, API readback, lineage, coverage, DQ, consumer evidence, independent review, and merge.

## 6. Current Baseline Facts

At `0d1603cf347e30c9cf2f25f0eecc10673ac55015`:

- `modules/market_intelligence` does not exist.
- `modules/external_data` has reusable control-plane and ingestion scaffolding.
- Geo, HeatZone, and SiteScore paths contain unsafe missing/default behavior.
- Source snapshots are tenant-first.
- The provider registry is metadata-only.
- Relation names/grains require single-writer reconciliation.
- Learning Hub already provides governed dataset/model release primitives.
- The repo already includes dlt, Dagster, DuckDB, H3, MLflow, CatBoost, LightGBM, Great Expectations, Evidently, and optimization libraries; do not create a duplicate platform without an ADR.

## 7. Immediate Dispatch

```text
EMGI-KRN-MEAS-001
EMGI-KRN-DATASET-001
EMGI-KRN-SCOPE-001
EMGI-KRN-OBS-001
EMGI-KRN-TIME-001
EMGI-KRN-MANIFEST-001
EMGI-KRN-RELATION-001
EMGI-KRN-READINESS-001
EMGI-SAFE-GEO-001
EMGI-SAFE-HEATZONE-001
EMGI-SAFE-SITESCORE-001
EMGI-TEST-CORPUS-001
```

Source, domain, API, UI, model, data-product, and verification tasks may start from contract fixtures in parallel. They do not wait for live connectors.

## 8. Integration Gates

```text
G0 CONTRACT_LOCK
G1 GENERATED_REGISTRY
G2 SCHEMA_AND_RELATION_ASSEMBLY
G3 REAL_SOURCE_REPLAY
G4 100_ADDRESS_END_TO_END
G5 1000_ADDRESS_REGRESSION
G6 SHADOW_MODEL_AND_DECISION_EVIDENCE
G7 GOVERNED_RELEASE
```

## 9. Definition of Done

A branch, local test, fixture, commit, or opened PR is not completion. Completion requires implementation, negative and contract tests, exact evidence, durable readback where applicable, independent approval, merge to `dev`, and orchestrator terminal closeout.

## 10. Research Basis

The package preserves and revises `ODP-EMGI-PRODUCT-001@0.1.0` and `ODP-EMGI-IMPLEMENTATION-001@0.1.0`, aligns them with the broader ODay Plus specification and current repository, and records source/OSS findings reviewed on 2026-08-13.

Provider terms, releases, endpoints, quotas, coverage, and licenses must be re-verified in each source task's dated feasibility receipt.
