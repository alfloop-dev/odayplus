---
doc_id: ODP-EMGI-FLEET-EXEC-003-1
title: EMGI v0.3 Multi-LLM Parallel Execution — Part 1
version: 0.3.0
status: approved-for-parallel-implementation
language: zh-TW
part: 1
part_count: 2
source_document: ODP-EMGI-FLEET-EXEC-003
reviewed_baseline_sha: 0d1603cf347e30c9cf2f25f0eecc10673ac55015
updated_at: 2026-08-13
---

# ODay Plus EMGI Multi-LLM Parallel Execution Tasks

## 1. Objective

Implement the EMGI v0.3.0 design as production contracts, code, migrations, source plugins, domain logic, data products, API/UI, model pipelines, verification evidence, and governed release artifacts.

Machine-readable dispatch authority:

`docs/design/emgi/v0.3/tasks/manifest.json`

## 2. Execution Model

This is not a human calendar plan. After the contract seed is available, source, domain, data-product, API, UI, testing, and model workers may proceed in parallel from fixtures.

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

## 3. Binding Rules

- Start from `origin/dev@0d1603cf347e30c9cf2f25f0eecc10673ac55015` or a later recorded `dev` SHA.
- Use `task/<task-id>` and target `dev`.
- Owner and reviewer must differ.
- Do not edit generated-only central files unless the task is the designated integration task.
- A missing value is not zero. A zero requires negative-evidence proof.
- `CURRENT_TIMESTAMP`, table names, fake tenants, `(0,0)`, confidence defaults, and fixture fallback cannot establish production truth.
- HeatZone/SiteScore v1 remain shadow-only.
- Physical feasibility and economics can block a decision even when a model score exists.
- Source work closes only with replay and durable readback evidence.
- Evidence belongs under `docs/evidence/completion/<task-id>/`.

## 4. Dispatch Inventory

### Kernel
| Task | Priority | Contract dependencies | Complete responsibility |
|---|---|---|---|
| `EMGI-KRN-MEAS-001` | P0 | none | Represent observed, partial, stale, not-collected, not-licensed, not-authorized, missing-unexpectedly, quarantined, source-error, truncated, and saturated states without coercing them to zero. |
| `EMGI-KRN-DATASET-001` | P0 | none | Model provider organizations separately from concrete source datasets and releases. |
| `EMGI-KRN-SCOPE-001` | P0 | EMGI-KRN-DATASET-001 | Represent owner, sharing, sensitivity, scope ID, and purpose grants as orthogonal dimensions. |
| `EMGI-KRN-OBS-001` | P0 | EMGI-KRN-DATASET-001, EMGI-KRN-SCOPE-001 | Deduplicate identical bytes while preserving repeated observations and last-seen evidence. |
| `EMGI-KRN-TIME-001` | P0 | none | Separate event, effective, published, observed, fetched, ingested, available, knowledge, prediction-origin, label-maturity, retracted, and build times. |
| `EMGI-KRN-MANIFEST-001` | P0 | EMGI-KRN-MEAS-001, EMGI-KRN-OBS-001, EMGI-KRN-TIME-001 | Keep feature evidence, future outcome evidence, and combined training provenance in separate immutable manifests. |
| `EMGI-KRN-RELATION-001` | P0 | none | Register every EMGI/model relation with one grain, one purpose, and one authoritative writer. |
| `EMGI-KRN-READINESS-001` | P0 | EMGI-KRN-MEAS-001 | Implement DISCOVERY_READY, SCREENING_READY, COMPARISON_READY, DUE_DILIGENCE_READY, and MODEL_TRAINING_READY. |
| `EMGI-KRN-COVERAGE-001` | P0 | EMGI-KRN-OBS-001, EMGI-KRN-TIME-001, EMGI-KRN-MEAS-001 | Prove coverage at dataset, scope, tenant, entity, business-date, geometry, and time-window levels. |

### Safety
| Task | Priority | Contract dependencies | Complete responsibility |
|---|---|---|---|
| `EMGI-SAFE-GEO-001` | P0 | EMGI-KRN-MEAS-001 | Represent unresolved geocode without a fabricated coordinate. |
| `EMGI-SAFE-HEATZONE-001` | P0 | EMGI-KRN-MEAS-001, EMGI-KRN-READINESS-001 | Mark heuristic v1 as shadow-only and prohibit binding decision use. |
| `EMGI-SAFE-SITESCORE-001` | P0 | EMGI-KRN-MEAS-001, EMGI-KRN-READINESS-001 | Separate model score availability from recommendation readiness. |
| `EMGI-SAFE-PIT-001` | P0 | EMGI-KRN-TIME-001, EMGI-KRN-COVERAGE-001, EMGI-KRN-MANIFEST-001, EMGI-KRN-RELATION-001 | Use store-local business dates and explicit training_as_of. |

### Sources
| Task | Priority | Contract dependencies | Complete responsibility |
|---|---|---|---|
| `EMGI-SRC-RIS-NLSC-001` | P0 | EMGI-KRN-DATASET-001, EMGI-KRN-SCOPE-001, EMGI-KRN-OBS-001 | Capture immutable raw releases and publish dataset versions. |
| `EMGI-SRC-TGOS-001` | P0 | EMGI-KRN-DATASET-001, EMGI-KRN-OBS-001 | Support exact, fuzzy, no-match, multiple-result, malformed, rate-limit, and stale-response cases. |
| `EMGI-SRC-OPEN-POI-001` | P0 | EMGI-KRN-DATASET-001, EMGI-KRN-OBS-001 | Mirror adopted Overture releases with checksum, changelog, bridge files, and dual taxonomy support. |
| `EMGI-SRC-MOF-MOI-001` | P0 | EMGI-KRN-DATASET-001, EMGI-KRN-OBS-001 | Keep MOI sales and rental parsers in separate packages and contracts. |
| `EMGI-SRC-OSM-TDX-001` | P0 | EMGI-KRN-DATASET-001, EMGI-KRN-OBS-001, EMGI-KRN-TIME-001 | Version OSM PBF/replication assets separately from routing graphs. |
| `EMGI-SRC-LISTING-001` | P0 | EMGI-KRN-OBS-001, EMGI-KRN-SCOPE-001 | Support API/feed, embedded JSON, network response, DOM, adaptive-selector, LLM fallback, browser capture, mobile share, email/LINE intake, and change watching as pluggable modes. |
| `EMGI-SRC-MOBILITY-001` | P0 | EMGI-KRN-TIME-001, EMGI-KRN-COVERAGE-001 | Ingest available public transit/bike OD as observed source-specific flows. |
| `EMGI-SRC-CWA-EVENTS-001` | P0 | EMGI-KRN-DATASET-001, EMGI-KRN-OBS-001 | Separate weather/operating context from market-intelligence bounded context while reusing the same external kernel. |

### Survey
| Task | Priority | Contract dependencies | Complete responsibility |
|---|---|---|---|
| `EMGI-SURVEY-001` | P0 | EMGI-KRN-SCOPE-001, EMGI-KRN-OBS-001, EMGI-KRN-READINESS-001 | Implement assignment, target binding, ODK submission sync, evidence, reviewer separation, correction, expiry, and resurvey. |

### Domains
| Task | Priority | Contract dependencies | Complete responsibility |
|---|---|---|---|
| `EMGI-DOM-GEO-001` | P0 | EMGI-KRN-MEAS-001, EMGI-KRN-MANIFEST-001, EMGI-SRC-RIS-NLSC-001, EMGI-SRC-TGOS-001 | Implement source-native geometry, versioned address candidates, admin cross-check, redirects, and manual authority. |
| `EMGI-DOM-POI-COMP-001` | P0 | EMGI-KRN-MEAS-001, EMGI-KRN-COVERAGE-001, EMGI-SRC-OPEN-POI-001, EMGI-SRC-MOF-MOI-001 | Implement Splink-compatible probability bands, review-required state, reversible identity edges, and source independence. |
| `EMGI-DOM-PROPERTY-RENT-001` | P0 | EMGI-KRN-MEAS-001, EMGI-KRN-COVERAGE-001, EMGI-SRC-MOF-MOI-001, EMGI-SRC-LISTING-001 | Keep source listing observation, governed listing identity, property identity, revision, and status history distinct. |
| `EMGI-DOM-TRANSPORT-001` | P0 | EMGI-KRN-MEAS-001, EMGI-KRN-COVERAGE-001, EMGI-SRC-OSM-TDX-001 | Implement provider-neutral route/matrix/isochrone/map-match interface and graph version. |
| `EMGI-DOM-FEASIBILITY-001` | P0 | EMGI-KRN-MEAS-001, EMGI-KRN-READINESS-001, EMGI-SURVEY-001 | Represent legal use, zoning, floor, frontage, power, water, drainage, gas, ventilation, exhaust, flood risk, loading, temporary stop, building restrictions, and construction feasibility. |
| `EMGI-DOM-ECONOMICS-001` | P0 | EMGI-KRN-MEAS-001, EMGI-KRN-READINESS-001 | Version machine mix, area, utility, price, CAPEX, deposit, fitout, OPEX, financing, tax, working capital, and residual-value assumptions. |

### Data Products
| Task | Priority | Contract dependencies | Complete responsibility |
|---|---|---|---|
| `EMGI-DP-COVERAGE-001` | P0 | EMGI-KRN-COVERAGE-001, EMGI-KRN-MANIFEST-001, EMGI-KRN-READINESS-001 | Publish domain coverage, freshness, search completeness, uncertainty, and readiness blockers over space/time. |
| `EMGI-DP-MARKET-CELL-001` | P0 | EMGI-DOM-GEO-001, EMGI-DOM-POI-COMP-001, EMGI-DOM-PROPERTY-RENT-001, EMGI-DP-COVERAGE-001 | Use source-native support and declared allocation methods before H3 aggregation. |
| `EMGI-DP-CATCHMENT-001` | P0 | EMGI-DOM-TRANSPORT-001, EMGI-DOM-GEO-001 | Aggregate population, POI, competitor capacity, rent, listing, mobility, traffic, parking, and coverage over versioned catchments. |
| `EMGI-DP-SITE-CONTEXT-001` | P0 | EMGI-DP-COVERAGE-001, EMGI-DP-MARKET-CELL-001, EMGI-DP-CATCHMENT-001, EMGI-DOM-FEASIBILITY-001 | Build deterministic component manifests for identity, catchments, demographics, built environment, POI, competitors, rent, listings, mobility, traffic, events, survey, feasibility, and coverage. |
| `EMGI-DP-ACQUISITION-001` | P0 | EMGI-DP-COVERAGE-001, EMGI-DP-SITE-CONTEXT-001 | Prioritize missing observations by candidate priority, ranking margin, expected uncertainty reduction, source cost, latency, quota, and survey effort. |

### API and UI
| Task | Priority | Contract dependencies | Complete responsibility |
|---|---|---|---|
| `EMGI-API-001` | P0 | EMGI-KRN-MANIFEST-001, EMGI-KRN-READINESS-001 | Define market explorer, site context build/read/compare, competitor, listing observation, survey, data health, coverage, lineage, data-gap, and acquisition-plan APIs. |
| `EMGI-UI-001` | P0 | EMGI-API-001 | Build against generated client and Prism fixtures without inventing fields. |

### Models
| Task | Priority | Contract dependencies | Complete responsibility |
|---|---|---|---|
| `EMGI-MODEL-HEATZONE-V3-001` | P0 | EMGI-DP-MARKET-CELL-001, EMGI-DP-CATCHMENT-001, EMGI-KRN-MANIFEST-001 | Use population, households, residential, POI, competitor capacity, rent, listing opportunity, own-store capacity, catchment, and coverage features. |
| `EMGI-MODEL-SITESCORE-V3-001` | P0 | EMGI-DP-SITE-CONTEXT-001, EMGI-DOM-ECONOMICS-001, EMGI-SAFE-SITESCORE-001, EMGI-KRN-MANIFEST-001 | Implement separate MarketPotential, TargetFormatCounterfactual, RampCurve, Cannibalization, UnitEconomics, and DecisionPolicy components. |
| `EMGI-MODEL-UNCERTAINTY-001` | P0 | EMGI-MODEL-HEATZONE-V3-001, EMGI-MODEL-SITESCORE-V3-001 | Pilot conformal/MAPIE-compatible intervals on held-out data rather than fixed spreads. |
| `EMGI-MODEL-VALIDATION-001` | P0 | EMGI-MODEL-UNCERTAINTY-001, EMGI-KRN-COVERAGE-001 | Generate forward-time, spatial-block, leave-region-out, leave-brand/tenant-out, and new-store-cohort results. |

### Integration
| Task | Priority | Contract dependencies | Complete responsibility |
|---|---|---|---|
| `EMGI-INTEGRATION-REGISTRY-001` | P0 | EMGI-KRN-DATASET-001, EMGI-KRN-SCOPE-001, EMGI-KRN-OBS-001, EMGI-KRN-MANIFEST-001, EMGI-KRN-RELATION-001 | Generate central registries from task-owned fragments; reject duplicate IDs and incompatible versions. |
| `EMGI-INTEGRATION-CROSSREPO-001` | P0 | none | Define store reference, daily performance, machine capacity, store coverage, and operational-start observation contracts. |
| `EMGI-INTEGRATION-ROUTING-001` | P0 | EMGI-DOM-TRANSPORT-001, EMGI-TEST-CORPUS-001 | Compare Valhalla, GraphHopper, openrouteservice, Google comparator, and FMM where applicable. |

### Verification
| Task | Priority | Contract dependencies | Complete responsibility |
|---|---|---|---|
| `EMGI-TEST-CORPUS-001` | P0 | none | Cover Taiwan address variants, legacy county names, sections/lanes/alleys/subnumbers/floors/basements, rural/island cases, invalid addresses, cross-admin errors, and multi-unit buildings. |
| `EMGI-INTEGRATION-LIVE-READBACK-001` | P0 | EMGI-INTEGRATION-REGISTRY-001, EMGI-TEST-CORPUS-001 | For adopted sources prove live or approved sample fetch, raw checksum, canonical mapping, DQ, persistence, restart, API readback, freshness, lineage, and consumer readback. |

### Release
| Task | Priority | Contract dependencies | Complete responsibility |
|---|---|---|---|
| `EMGI-RELEASE-001` | P0 | EMGI-INTEGRATION-LIVE-READBACK-001, EMGI-MODEL-VALIDATION-001, EMGI-INTEGRATION-CROSSREPO-001, EMGI-UI-001, EMGI-DP-ACQUISITION-001 | Complete shadow comparison, source canary, tenant/area canary, UAT, restore, rollback, kill-switch, and consumer compatibility. |

## 5. Immediate Contract Seed

The following tasks can be dispatched together immediately:

```text
EMGI-KRN-MEAS-001
EMGI-KRN-DATASET-001
EMGI-KRN-TIME-001
EMGI-KRN-RELATION-001
EMGI-KRN-READINESS-001
EMGI-TEST-CORPUS-001
```

As soon as their draft contracts and fixtures are published, dependent tasks can start before merge by pinning the exact contract commit in their task manifest. Claimable product behavior still requires the declared gate.

## 6. Current-Code Safety Set

These tasks are not optional refactoring. They prevent the current baseline from converting data absence into favorable market or investment signals:

```text
EMGI-SAFE-GEO-001
EMGI-SAFE-HEATZONE-001
EMGI-SAFE-SITESCORE-001
EMGI-SAFE-PIT-001
```

No new EMGI connector should be advertised as improving production decisions while these unsafe compatibility paths remain binding.
