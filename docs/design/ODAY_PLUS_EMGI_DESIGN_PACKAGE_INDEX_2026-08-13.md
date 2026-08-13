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

It consolidates the previously proposed product definition, source-feasibility blueprint, repository inspection, current implementation defects, official web-source verification, and open-source project review into a single contract stack that Codex, Claude, Antigravity, and other workers can implement in parallel.

The package does **not** claim that the current HeatZone, SiteScore, market-intelligence data products, external providers, or source coverage are already production-ready. It defines the target contracts, the fail-closed migration path, and the exact work boundaries required to make those claims true.

## 2. Binding Documents and Precedence

When documents conflict, apply them in this order:

1. `ODAY_PLUS_EXTERNAL_MARKET_AND_GEOGRAPHIC_INTELLIGENCE_PRODUCT_AND_DATA_PRODUCT_DEFINITION_v0.3.0.md`
2. `ODAY_PLUS_EMGI_SOURCE_FEASIBILITY_AND_IMPLEMENTATION_BLUEPRINT_v0.3.0.md`
3. `ODAY_PLUS_EMGI_SOURCE_AND_OPEN_SOURCE_DECISION_MATRIX_2026-08-13.md`
4. `ODAY_PLUS_EMGI_REVIEW_MANIFEST.yaml`
5. `ODAY_PLUS_EMGI_LLM_PARALLEL_EXECUTION_TASKS_2026-08-13.json`
6. `ODAY_PLUS_EMGI_LLM_PARALLEL_EXECUTION_TASKS_2026-08-13.md`

The machine-readable task JSON is authoritative for dispatch fields, owned paths, contract dependencies, acceptance clauses, and verification commands. The Markdown task guide explains intent and integration gates.

## 3. Documents in This Commit

| File | Role |
|---|---|
| `ODAY_PLUS_EXTERNAL_MARKET_AND_GEOGRAPHIC_INTELLIGENCE_PRODUCT_AND_DATA_PRODUCT_DEFINITION_v0.3.0.md` | Product boundary, users, jobs, data products, readiness, non-goals, ADRs |
| `ODAY_PLUS_EMGI_SOURCE_FEASIBILITY_AND_IMPLEMENTATION_BLUEPRINT_v0.3.0.md` | Current-repo findings, target architecture, canonical contracts, migration and release gates |
| `ODAY_PLUS_EMGI_SOURCE_AND_OPEN_SOURCE_DECISION_MATRIX_2026-08-13.md` | Verified source strategy, open-source adoption, role/dependency rules, policy warnings |
| `ODAY_PLUS_EMGI_LLM_PARALLEL_EXECUTION_TASKS_2026-08-13.md` | Human-readable multi-LLM dispatch and completion rules |
| `ODAY_PLUS_EMGI_LLM_PARALLEL_EXECUTION_TASKS_2026-08-13.json` | Machine-readable task graph |
| `ODAY_PLUS_EMGI_REVIEW_MANIFEST.yaml` | Normative stack, known conflicts, superseded assumptions, review gates |
| `ODAY_PLUS_EMGI_RELATION_OWNERSHIP_BASELINE.yaml` | Current and target relation ownership, grain, writer, and collision controls |

## 4. Superseded Assumptions

This package supersedes the following assumptions from the v0.1.0 drafts or current baseline implementation:

- A 12-week, Sprint 0-5, sequential PR plan.
- A single `Dataset Scope` enum that mixes ownership, sharing, sensitivity, and purpose.
- Treating platform-wide public data as a fake tenant-owned dataset.
- Treating a provider registry row as proof that a concrete dataset is live.
- Treating missing POI, competitor, listing, rent, geocode, or confidence values as numeric zero or confidence one.
- Treating a same-H3 count as a 500-meter or travel-time feature.
- Treating table names as immutable source snapshot IDs.
- Treating one successful platform ingestion partition as complete coverage for every tenant and every store.
- Cutting Taiwan business days in UTC.
- Using the same relation name for current candidate scoring rows and historical opened-store training outcomes.
- Letting a model-input gate validate only demand features while downstream GO/WAIT/REJECT uses unvalidated rent, CAPEX, margin, feasibility, and cannibalization defaults.
- Treating the existing HeatZone and SiteScore heuristic paths as binding production decision systems.
- Buying telecom mobility, national traffic, or a full listing feed before a low-cost baseline and incremental value experiment exist.
- Waiting for one source or one backend before API, UI, tests, domain logic, and data products can start.

## 5. Non-Negotiable Implementation Rules

1. **Start from the exact reviewed `dev` SHA or a later fetched `dev`; never start from stale `main`.**
2. Every task branch targets `dev` and uses `task/<task-id>` unless the orchestrator applies an equivalent governed convention.
3. An owner cannot approve or finalize its own task.
4. Central registries, aggregate OpenAPI bundles, contract locks, and ordered migrations are generated or assembled by their designated integration tasks only.
5. Missing, partial, stale, unlicensed, unauthorized, quarantined, truncated, and source-error states never silently become zero.
6. A zero aggregate is valid only when negative evidence and search or partition completeness are proven.
7. Feature-time, knowledge-time, label-time, and business-time are separate contracts.
8. Feature source manifests never contain future label evidence.
9. A model score can exist while a decision is not ready. Incomplete economics or physical feasibility must block binding recommendation.
10. HeatZone and SiteScore baseline heuristics remain shadow-only until their v3 contracts and release gates pass.
11. Source technical readiness and policy readiness are separate. Policy warnings do not block technical adapter research, fixtures, replay, or shadow evaluation; publication remains gated.
12. Discovery adapters may include API, files, partner feeds, browser capture, crawlers, watchers, and surveys. They publish observations, not unquestioned truth.
13. External source disagreements and upstream dependency are preserved. Duplicate downstream copies do not count as independent evidence.
14. Cross-brand data is analytical-only unless an explicit management or display grant exists.
15. Runtime completion requires durable persistence, restart readback, API readback, lineage, coverage, DQ, and consumer evidence—not only unit tests or fixture success.

## 6. Current Baseline Facts Workers Must Not Re-Discover Incorrectly

At reviewed baseline `0d1603cf347e30c9cf2f25f0eecc10673ac55015`:

- `modules/market_intelligence` does not yet exist.
- `modules/external_data` contains useful control-plane, ingestion, snapshot, source-policy, and geo scaffolding that must be migrated and composed rather than replaced wholesale.
- `GeoFeatureSnapshot` and the HeatZone/SiteScore input paths contain missing-to-zero and default-confidence behavior that must be removed or isolated.
- `SourceSnapshotService` is tenant-first and derives object identity and paths from tenant scope.
- `provider_registry.py` explicitly describes itself as metadata-only; it is not live-dataset proof.
- The dbt landing zone and model installer contain relations whose names and grains must be reconciled under single-writer ownership.
- The Learning Hub already supports dataset snapshots, model artifacts, temporal validation, approval, and governed release. EMGI should extend those contracts rather than create another model registry.
- `pyproject.toml` already includes dlt, Dagster, dbt-compatible data tooling, ML libraries, PostGIS/H3 dependencies, Great Expectations, Evidently, MLflow, CatBoost, LightGBM, and optimization libraries. Do not add a second orchestration or registry stack without an approved ADR.

## 7. Safe First Dispatch Set

The following tasks have no implementation dependency on a live external provider and may start immediately after the package is merged:

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

Source, domain, API, UI, and data-product tasks may also start from the contract fixtures declared in the task graph. They do not wait for the real connectors.

## 8. Integration Gates

Only these gates serialize the fleet:

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

A task dependency should normally point to a contract version or one of these gates—not to an arbitrary previous PR.

## 9. Definition of Done

A task is complete only when:

- the intended implementation exists;
- focused tests pass;
- contract and negative tests pass;
- evidence records exact SHA, commands, environment, results, limitations, and rollback;
- source or runtime work proves durable restart readback where applicable;
- an independent reviewer approves;
- the PR is merged to `dev`;
- the orchestrator records terminal completion.

A branch, commit, locally green test, generated fixture, or opened PR is not completion.

## 10. Source Basis

The v0.3.0 package preserves and revises the uploaded v0.1.0 product definition and implementation blueprint, and aligns them with the broader ODay Plus system specification, the current repository baseline, official source documentation reviewed on 2026-08-13, and the open-source projects named in the source decision matrix.

Research facts and provider terms remain subject to re-verification at implementation time. Each source task must store a dated feasibility receipt rather than treating this document as a permanent substitute for the upstream contract.
