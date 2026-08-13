---
doc_id: ODP-EMGI-SOURCE-MATRIX-003-2
title: EMGI v0.3 Source and OSS Decision Matrix — Part 2
version: 0.3.0
status: approved-for-parallel-implementation
language: zh-TW
part: 2
part_count: 2
source_document: ODP-EMGI-SOURCE-MATRIX-003
reviewed_baseline_sha: 0d1603cf347e30c9cf2f25f0eecc10673ac55015
updated_at: 2026-08-13
---

> Research was checked on 2026-08-13. Each implementation task must re-verify upstream versions, terms, endpoints, and coverage in a dated feasibility receipt.

# ODay Plus EMGI Source and Open-Source Decision Matrix

## 9. Market Events, Documents and Change

| Project / channel | Role | Decision |
|---|---|---|
| Government RSS / APIs / source pages | DISCOVERY | ADOPT_NOW |
| RSSHub | FEED NORMALIZATION | PILOT_CHALLENGER |
| changedetection.io | CHANGE DETECTION | ADOPT_NOW |
| Browsertrix | EVIDENCE SNAPSHOT | PILOT_CHALLENGER |
| PDF / HTML structured extraction | DOCUMENT PARSING | ADOPT_AS_BASELINE |
| LLM extraction | EVENT CANDIDATE | PILOT_CHALLENGER |
| Human review | AUTHORITY | REQUIRED |

LLM output is a candidate only. Market event state distinguishes announcement, start, expected completion, actual completion, delay, cancellation, and supersession.

## 10. Survey and Field Operations

| Project | Role | Decision |
|---|---|---|
| ODK Central / Collect | GENERAL OFFLINE SURVEY | ADOPT_NOW |
| QField / QFieldCloud | GIS FIELD EDITING | PILOT_CHALLENGER |
| Grounding DINO | ZERO-SHOT LABEL BOOTSTRAP | PILOT_CHALLENGER |
| PaddleOCR | EQUIPMENT / PRICE TEXT | PILOT_CHALLENGER |
| Smaller fine-tuned detector | PRODUCTION CAMERA MODEL | FUTURE AFTER LABEL SET |

ODK handles forms, counts, photos, common attributes. QField handles professional geometry edits. EMGI remains owner of assignment, review, expiry, evidence binding, and promotion.

References:

- https://github.com/getodk/central
- https://github.com/opengisch/QField

## 11. Spatial Optimization and Candidate Generation

| Project | Role | Decision |
|---|---|---|
| PySAL `spopt` | FACILITY LOCATION / COVERAGE | ADOPT_AS_BASELINE |
| OR-Tools / Pyomo | NETWORK OPTIMIZATION | REUSE CURRENT DEPENDENCIES |
| SRAI | SPATIAL EMBEDDING | PILOT_CHALLENGER |

Correct flow:

```text
demand surface
→ uncovered demand
→ facility-location candidate cells
→ available listings
→ SiteScore
→ NetPlan
```

Do not run expensive routing and paid source queries for every Taiwan H3 cell.

## 12. Map Delivery

| Project | Role | Decision |
|---|---|---|
| MapLibre GL JS | FRONTEND MAP | ADOPT_NOW |
| PMTiles | STATIC / VERSIONED LAYERS | ADOPT_NOW |
| Martin | DYNAMIC POSTGIS TILES | ADOPT_NOW |

Static monthly profiles and historical snapshots use PMTiles. Dynamic listings, survey, candidates, and review queues use Martin.

## 13. Data and API Contracts

| Project / capability | Decision |
|---|---|
| Existing Dagster | REUSE |
| Existing dlt | REUSE |
| Existing dbt | REUSE |
| Data Contract CLI / ODCS-compatible export | PILOT, do not replace current contracts |
| Prism | ADOPT for OpenAPI mocks |
| Schemathesis | ADOPT for property/stateful API tests |
| OpenLineage | PILOT for pipeline lineage |
| MLflow / Learning Hub | REUSE |
| Atlas | DEFER; use only if migration-fragment collisions cannot be solved by current process |

## 14. Uncertainty and Outcome Models

| Project | Role | Decision |
|---|---|---|
| MAPIE | CONFORMAL INTERVAL PILOT | PILOT_CHALLENGER |
| scikit-survival | CENSORED PAYBACK / TIME-TO-EVENT | PILOT_CHALLENGER |
| LightGBM / CatBoost | TABULAR BASELINES | REUSE |
| Graph / embedding models | RESEARCH | DEFER UNTIL BASELINE EVIDENCE |

Prediction intervals require grouped/time-aware calibration and abstention. A fixed percentage spread is not a validated interval.

References:

- https://github.com/scikit-learn-contrib/MAPIE
- https://github.com/sebp/scikit-survival

## 15. Immediate Source Priorities

### Adopt first

```text
RIS
NLSC
TGOS
MOF tax registry
MOI rental
OSM
Overture
Foursquare OS sample
TDX parking / VD coverage
ODK
browser/mobile listing intake
```

### Parallel pilots

```text
routing benchmark
POI coverage benchmark
listing crawler adapters
bus-probe map matching
camera counts
synthetic OD
commercial mobility uplift
Mapillary prefill
QField geometry survey
conformal intervals
censored payback
```

### Do not purchase first

```text
full Taiwan telecom mobility
full national lane-level live traffic
one commercial POI as sole truth
one full national listing feed
```

These are bought only after `source_value_experiment` demonstrates incremental decision value.

## 16. Required Feasibility Receipt

Each source task closes only with:

```text
upstream URL / release
review date
authentication
sample checksum
schema profile
spatial and temporal coverage
null and duplicate rates
identity join rate
search completeness behavior
quota / cost
retention and derivative warning
failure modes
technical readiness
policy state
publication recommendation
```
