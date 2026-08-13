---
doc_id: ODP-EMGI-IMPLEMENTATION-001-A
title: EMGI v0.3 System — Current Baseline, Architecture and Common Contracts
version: 0.3.0
status: approved-for-parallel-implementation
language: zh-TW
part: 1
part_count: 3
source_document: ODP-EMGI-IMPLEMENTATION-001@0.3.0
reviewed_baseline_sha: 0d1603cf347e30c9cf2f25f0eecc10673ac55015
updated_at: 2026-08-13
---

> Binding part 1 of 3. Read together with all parts listed in the EMGI package index.

# ODay Plus EMGI 資料來源可行性調查與實作藍圖

## 0. 文件目的

本文件把產品定義轉成可由多個 LLM 同時實作的 target architecture、現況修正、資料契約、來源 plugin、資料產品、模型邊界、整合 gate 與驗收規則。

本版本不再使用：

```text
12 週
Sprint 0–5
EMGI-001 → EMGI-015 線性 PR
等前一位工程師完成後才開始
```

實作採：

```text
Contract Seed
→ Parallel Source / Domain / Product / API / UI / Test lanes
→ Generated Registry
→ Schema Assembly
→ Real Source Replay
→ End-to-End and Governed Release Gates
```

---

# 1. Current Repository Baseline

Reviewed baseline:

```text
repository: alfloop-dev/odayplus
branch: dev
sha: 0d1603cf347e30c9cf2f25f0eecc10673ac55015
```

## 1.1 可沿用能力

現有 `modules/external_data` 已有：

- provider metadata；
- fixture／live mode；
- credential inventory；
- license metadata；
- source allowlist；
- manual／scheduled fetch；
- idempotency、retry、circuit breaker；
- tenant-scoped ingestion runs；
- raw snapshot、checksum、retention、residency；
- quarantine、freshness、lineage；
- address normalization、geocode、H3 R8/R9/R10；
- canonical POI、competitor、listing connector scaffolding。

現有 Learning Hub 已有：

- model-ready relation inventory；
- dataset snapshot；
- immutable model／validation artifact；
- temporal validation；
- MLflow registry adapter；
- approval and promotion saga；
- model card and rollback metadata。

現有 dependencies 已包含：

```text
dlt
Dagster
DuckDB
PostgreSQL / PostGIS
H3
Great Expectations
Evidently
MLflow
CatBoost
LightGBM
OR-Tools
CVXPY / Pyomo
```

不得因 EMGI 再建立另一套 orchestration、model registry 或 generic ingestion framework。

## 1.2 現況 P0 缺陷

### A. Missing-to-zero

`GeoFeatureSnapshot`、HeatZone 與 SiteScore baseline input 仍有：

```text
missing POI → 0
missing competitor → 0
missing listing → 0
missing rent → 0
missing confidence → 1 or 0 depending path
unresolved geocode → (0, 0)
```

這會把來源缺漏解讀成市場缺口或低成本。

### B. HeatZone heuristic 語意

Current baseline 使用固定常數及權重，且缺競店資料會提高 competition gap。該路徑只能標示：

```text
heatzone-heuristic-shadow-v1
decision_use = PROHIBITED
```

### C. SiteScore demand／decision 契約斷裂

Production model row 驗證 demand-side features，但 recommendation 另使用 rent、CAPEX、margin、cannibalization、comparables、confidence 等帶預設值欄位。模型分數可通過，最終決策卻可能由未驗證 default 決定。

### D. Relation 名稱與 grain collision

`candidate_site_view` 可能同時代表：

```text
current candidate scoring rows
historical opened-store training outcomes
```

同 relation 不得有多個 writer 或不同 grain。

### E. 偽 500m 特徵

Same-H3 aggregation 不得命名為 `_500m` 或 travel-time catchment。

### F. 假 PIT

部分 view 使用 `CURRENT_TIMESTAMP` 或 table names 作 source snapshot；Feature／Label lineage 未完全分離。

### G. Coverage false zero

平台某日 ingestion 成功不代表每個 tenant／store／date 完整。合法 zero label 需要 entity partition coverage。

### H. UTC business day

臺灣交易不得以 UTC 直接切 business date。

### I. Tenant-first global data

`SourceSnapshotService` 將 snapshot identity 與 object key 綁 tenant，與 global public data、licensed shared data 不相容。

### J. Observation dedup 過度

同一 bytes 的再次觀測仍有 `last_seen_at`、status persistence 與 listing／competitor 商業意義。

### K. 開店日 authority

沒有正式 `opened_on` 不得用 `created_at` 冒充；但可建立 operational start observations、proxy cohort 與 mature-store model。

### L. 回本公式不足

目前 heuristic 不是完整月度現金流模型，且 `999 months` 是 censored outcome，不應作普通連續標籤。

---

# 2. Target Architecture

```text
Discovery Plane
  API / files / partner feed / crawler / browser capture /
  watcher / document extraction / imagery / survey
        |
        v
External Data Control Plane
  Provider / Dataset / DatasetVersion / Adapter /
  ScopePrincipal / Policy / Cost / Health / Quota
        |
        v
Evidence Layer
  SourceContentBlob / SourceObservation /
  SourceSearchExecution / SourceRetraction /
  immutable GCS / GeoParquet / STAC for geo assets
        |
        v
Canonical Observation Plane
  Entity / Observation / IdentityEdge /
  ResolutionDecision / Bitemporal history /
  SourceDependency / Coverage
        |
        v
Data Product Plane
  dbt + YAML FeatureSpec + component manifests +
  dirty-region incremental rebuild
        |
        v
Decision Consumers
  Market Explorer / Property Radar / Survey /
  HeatZone v3 / SiteScore v3 / NetPlan / OpsBoard
```

## 2.1 Bounded Contexts

```text
modules/external_data
  control_plane/
  source_plugins/
  snapshots/
  observations/
  scope/
  policy/
  publication/
  lineage/

modules/market_intelligence
  geography/
  demographics/
  built_environment/
  poi/
  competitors/
  real_estate/
  mobility/
  transport/
  market_events/
  survey/
  identity/
  data_products/
  application/

modules/site_feasibility
  legal_use/
  utilities/
  construction/
  physical_site/

modules/operating_context
  weather/
  calendar/
  tariffs/
  hazards/
```

## 2.2 Storage

### Existing GCS Source Snapshot

普通 JSON／CSV／ZIP／HTML／WARC／evidence 由現有 snapshot service 演進，不另建重複 registry。

### GeoParquet

適合：

- Overture；
- Foursquare OS；
- NLSC／SEGIS geometry；
- OSM extracts；
- 大型 H3 history；
- offline analysis。

### STAC

只描述大型 geo／imagery／routing asset：

- source release；
- bbox／geometry；
- checksum；
- schema；
- storage URI；
- license and policy warning。

不使用 STAC 取代每頁 JSON ingestion metadata。

### PostgreSQL＋PostGIS＋H3

保存：

- canonical identity；
- observations；
- effective state；
- manual corrections；
- latest serving state；
- component metadata；
- dynamic vector tile source。

### dbt

保留為 canonical／mart／model-ready transformation owner。第一版 FeatureSpec 生成 dbt SQL、tests、docs，不自建全 backend compiler。

---

# 3. Common Contracts

## 3.1 Measurement Envelope

```json
{
  "value": null,
  "availability_status": "NOT_COLLECTED",
  "observation_count": 0,
  "coverage_ratio": 0.0,
  "freshness_status": "UNKNOWN",
  "uncertainty": null,
  "quality_flags": [],
  "source_manifest_id": null
}
```

Observed zero:

```json
{
  "value": 0,
  "availability_status": "OBSERVED",
  "observation_count": 12,
  "coverage_ratio": 1.0,
  "negative_evidence_valid": true,
  "source_manifest_id": "smf_..."
}
```

## 3.2 Provider / Dataset / Version

```text
external_control.providers
external_control.source_datasets
external_control.source_dataset_versions
external_control.source_contract_versions
external_control.source_adapters
```

Provider 是組織或平台；Dataset 是具體資料產品；Version 是 release／schema／endpoint／checksum。不能用 `provider_id = tdx` 代表所有 TDX data。

## 3.3 Scope Principal

```text
owner_scope
sharing_scope
sensitivity_class
scope_id
purpose_grants
```

Job scope 由 dataset registry 與 principal grant 決定。

## 3.4 Blob / Observation / Search

```text
source_content_blob
source_observation
source_search_execution
source_retraction
```

Search execution 保存：

```text
query_geometry
filters
partition_strategy
result_limit
returned_count
pagination_exhausted
saturated
coverage_status
source_health
```

## 3.5 Time

```text
effective_time
source_published_at
observed_at
available_at
knowledge_as_of
prediction_origin_time
label_maturity_time
retracted_at
store_timezone
business_day_boundary
```

Production materialization 缺 explicit as-of 時 fail，不可 fallback 到 `CURRENT_TIMESTAMP`。

## 3.6 Source and Dataset Manifests

```text
source_asset_manifest
feature_source_manifest
label_source_manifest
training_dataset_manifest
site_context_component_manifest
```

## 3.7 Relation Ownership

每個 relation 唯一聲明：

```text
relation_name
grain
purpose
authoritative_writer
schema_version
consumer_contracts
```

CI 阻擋多 writer 或 grain collision。

## 3.8 Readiness

```text
DISCOVERY_READY
SCREENING_READY
COMPARISON_READY
DUE_DILIGENCE_READY
MODEL_TRAINING_READY
```

---
