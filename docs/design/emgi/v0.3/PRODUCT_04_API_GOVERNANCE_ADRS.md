---
doc_id: ODP-EMGI-PRODUCT-001-D
title: EMGI v0.3 Product — API, Governance, ADRs and Acceptance
version: 0.3.0
status: approved-for-parallel-implementation
language: zh-TW
part: 4
part_count: 4
source_document: ODP-EMGI-PRODUCT-001@0.3.0
reviewed_baseline_sha: 0d1603cf347e30c9cf2f25f0eecc10673ac55015
updated_at: 2026-08-13
---

> Binding part 4 of 4. Read together with all parts listed in the EMGI package index.

# 11. API Surface

## 11.1 Market Explorer

```text
GET  /api/v1/market-intelligence/market-cells
GET  /api/v1/market-intelligence/market-zones
GET  /api/v1/market-intelligence/market-zones/{zone_id}
POST /api/v1/market-intelligence/market-zones/compare
```

## 11.2 Site Context

```text
POST /api/v1/market-intelligence/site-context-builds
GET  /api/v1/market-intelligence/site-context-builds/{job_id}
GET  /api/v1/market-intelligence/site-context-snapshots/{snapshot_id}
POST /api/v1/market-intelligence/site-context-snapshots/compare
```

Request：

```json
{
  "site_id": "candidate_or_address_id",
  "effective_as_of": "2026-08-13T00:00:00+08:00",
  "knowledge_as_of": "2026-08-13T00:00:00+08:00",
  "target_format_snapshot_id": "tfs_...",
  "catchment_policy_id": "laundry-tw-v3",
  "feature_policy_id": "emgi-site-context-v3",
  "required_readiness_level": "COMPARISON_READY"
}
```

## 11.3 Acquisition Plan

```text
POST /api/v1/market-intelligence/data-acquisition-plans
GET  /api/v1/market-intelligence/data-acquisition-plans/{id}
POST /api/v1/market-intelligence/data-gap-tasks/{id}/execute
```

## 11.4 Data Health

```text
GET /api/v1/external-data/sources
GET /api/v1/external-data/datasets
GET /api/v1/external-data/dataset-versions
GET /api/v1/external-data/ingestion-runs
GET /api/v1/external-data/search-executions
GET /api/v1/market-intelligence/data-products
GET /api/v1/market-intelligence/data-products/{id}/coverage
GET /api/v1/market-intelligence/data-products/{id}/lineage
```

---

# 12. Domain Events

最低事件：

```text
external.content.stored.v1
external.observation.recorded.v1
external.search.completed.v1
external.ingestion.completed.v1
external.record.quarantined.v1
external.source.retracted.v1
market.geo_reference.published.v1
market.demographic_profile.published.v1
market.poi.observed.v1
market.competitor.observed.v1
market.listing.observed.v1
market.event.observed.v1
market.survey.submitted.v1
market.survey.approved.v1
market.catchment.published.v1
market.site_context_component.published.v1
market.site_context.published.v1
market.coverage_surface.published.v1
market.data_gap.detected.v1
market.data_acquisition.requested.v1
market.change_signal.detected.v1
```

Event scope 使用 scope principal，不再只有 `tenant_id or dataset_scope` 的模糊二選一。

---

# 13. State Models

## 13.1 Source Dataset

```text
DISCOVERED
→ SAMPLE_CAPTURED
→ CONTRACT_VALIDATED
→ CONNECTOR_REPLAYABLE
→ BACKFILL_VERIFIED
→ LIVE_VERIFIED
→ PRODUCT_ACTIVE
```

Policy／publication 是獨立狀態。

## 13.2 Competitor／Listing Hysteresis

```text
ACTIVE
→ POSSIBLY_REMOVED
→ REMOVAL_LIKELY
→ VERIFIED_REMOVED
```

單次 miss 不直接關閉或下架。

## 13.3 Site Context Build

```text
REQUESTED
→ RESOLVING_IDENTITY
→ BUILDING_CATCHMENTS
→ RESOLVING_COMPONENTS
→ APPLYING_COVERAGE_POLICY
→ PUBLISHING
→ READY
```

例外：

```text
NEEDS_ADDRESS_REVIEW
NEEDS_SURVEY
PARTIAL_READY
DECISION_NOT_READY
FAILED
CANCELLED
```

---

# 14. Quality, Coverage and Hard Gates

每個資料產品回報：

```text
completeness
freshness
spatial_coverage
temporal_coverage
search_completeness
schema_validity
identity_resolution_quality
geocode_quality
source_consistency
source_independence
license_validity
lineage_completeness
```

## Site Context Hard Gates

- geocode acceptable；
- source-native geometry and H3；
- routing/catchment usable；
- basic demographics；
- POI search completeness known；
- competitor coverage known；
- rent benchmark or explicit unavailable；
- immutable manifests；
- readiness evaluation。

## Decision Hard Gates

Binding recommendation 另需：

- physical feasibility；
- target format；
- rent；
- CAPEX；
- operating cost assumptions；
- cannibalization；
- economic uncertainty；
- human policy approval。

---

# 15. Security, Privacy, License and Export

本版本保留來源政策與用途 binding，但將技術研究與 publication 分離。

禁止：

- 在 production 未經 scope principal 寫入資料；
- 將個人級裝置軌跡落庫；
- 將跨品牌敏感店級明細顯示給未授權使用者；
- license 過期後產生新的受限制衍生資料；
- 用 UI 隱藏取代 backend policy；
- 把 crawler observation 直接升格成 verified truth；
- 把 Google／商業內容複製為未核准永久主庫。

---

# 16. SLO and KPI

SLO 仍為提案值，由 Platform／SRE 核准：

| 能力 | 目標 |
|---|---|
| Cached cell/site read | P95 ≤ 2 秒 |
| Build acknowledgement | P95 ≤ 2 秒 |
| Site context build with available sources | P95 ≤ 5 分鐘 |
| Published lineage completeness | 100% |
| Silent stale／silent zero | 0 |
| Snapshot reproducibility | 100% |
| Read-path availability | ≥ 99.5% |
| Source failure isolation | 100% |
| License／policy kill switch | fail closed |

新增 KPI：

- zero-with-negative-evidence ratio；
- truncated-search rate；
- independent-source coverage；
- acquisition cost per decision-changing observation；
- abstention correctness；
- decision readiness conversion；
- component rebuild avoidance；
- cross-brand out-of-fold coverage。

---

# 17. Non-Goals

本版本不做：

- 把 current baseline heuristic 說成 production AI；
- 全臺每條巷子即時路況；
- 精確個人移動軌跡；
- 一開始購買所有商業資料；
- 用行政區或固定半徑取代服務圈；
- 把所有外部 context 塞入 market_intelligence；
- 讓 SiteScore 直接抓來源；
- 用 fixed 999 months 當未回本普通標籤；
- 用 tenant 名稱或 H3 ID 記憶訓練樣本；
- 用相同 relation 名稱承擔不同 grain；
- 因 blob 相同而丟掉後續 observation；
- 自動核准投資或物件 promotion。

---

# 18. Architecture Decision Register v0.3.0

| ID | 決策 |
|---|---|
| EMGI-001 | EMGI implementation home 為 `odayplus` |
| EMGI-002 | `modules/external_data` 為共同來源控制面 |
| EMGI-003 | 新增 `modules/market_intelligence` |
| EMGI-004 | Internal／external raw ingestion 分離，data product integration |
| EMGI-005 | Source-native geometry＋H3＋catchment＋market zone |
| EMGI-006 | H3 R9 為預設 cache grain，不是全域 canonical grain |
| EMGI-007 | Scope 改為 owner／sharing／sensitivity／purpose 四軸 |
| EMGI-008 | Measurement Envelope；missing 永不默認 0 |
| EMGI-009 | Site context 改為 component manifest |
| EMGI-010 | Discovery Sandbox 與 Production Publication 分離 |
| EMGI-011 | Content Blob 與 Observation 分離 |
| EMGI-012 | Canonical spatial store 為 PostGIS；大型 immutable geo asset 用 GeoParquet |
| EMGI-013 | STAC 只用於大型 geo／imagery asset，不取代一般 snapshot registry |
| EMGI-014 | EMGI 不輸出 binding investment decision |
| EMGI-015 | Cross-brand analytical-only，需 cohort／purpose grant |
| EMGI-016 | Survey 是正式 ground truth product |
| EMGI-017 | Unknown／expired／blocked publication fail closed |
| EMGI-018 | effective／knowledge／build／label time 分離 |
| EMGI-019 | Provider、Dataset、DatasetVersion 分離 |
| EMGI-020 | Source Role 與 Dependency Graph |
| EMGI-021 | Search Completeness 與 Negative Evidence |
| EMGI-022 | Technical Readiness 與 Policy State 分離 |
| EMGI-023 | Decision Readiness Levels |
| EMGI-024 | Physical Site Feasibility 獨立 hard gate |
| EMGI-025 | Site Economics 與 Market Potential 分離 |
| EMGI-026 | Dynamic Data Acquisition Planner |
| EMGI-027 | Dirty-region／component incremental rebuild |
| EMGI-028 | dbt＋YAML FeatureSpec；不先自建全 backend compiler |
| EMGI-029 | HeatZone／SiteScore heuristic shadow-only |
| EMGI-030 | Cross-fitted internal-demand calibration |
| EMGI-031 | Routing engine 由 Taiwan benchmark 決定 |
| EMGI-032 | Cross-repo frozen analytical contracts |
| EMGI-033 | Relation single-writer authority |
| EMGI-034 | Business timezone／calendar contract |
| EMGI-035 | Entity partition coverage 才能證明合法零值 |
| EMGI-036 | Feature、Label、Training manifests 分離 |
| EMGI-037 | Operational start observations，不以 created_at 冒充開店日 |
| EMGI-038 | Status inference 使用 hysteresis |
| EMGI-039 | Coverage-aware abstention |
| EMGI-040 | Claim Evidence Ledger 分離目標、實作與 live evidence |

---

# 19. MVP Acceptance Criteria

第一個 usable slice 必須：

1. 任一支援地址可建立 identity、geocode、H3 與 versioned catchment；
2. 顯示人口、戶數、POI、競店、租金、道路、listing、feasibility 與 coverage；
3. 每個數值可追 source observation／manifest；
4. 零值有 negative evidence；
5. 缺漏不補零；
6. 可建立 ODK／QField Survey；
7. 三個候選點在固定 policy／knowledge time 下可比較；
8. site context component 可增量重建；
9. source technical／policy／publication 狀態可查；
10. source failure 只造成明確 partial／stale／unavailable；
11. managed、transformation、cross-brand scopes 隔離；
12. SiteScore 只讀 versioned contract；
13. physical feasibility／economics 不完整時不得 binding GO；
14. 所有高風險修正有 actor、reason、before／after、audit；
15. 100-address smoke 與 1,000-address regression corpus 通過。

---

# 20. Required Approvals and Status

本文件已被使用者核准作多 LLM 平行實作 handoff，但下列 production authority 仍需各角色在任務 evidence 中完成：

- Product：產品邊界與 readiness；
- Expansion：找點、Survey、feasibility；
- Data Platform：跨 repo contract；
- GIS：routing、H3、geometry；
- Security／Privacy：scope principal、RLS、敏感欄位；
- Legal／Partnerships：publication policy；
- SRE：SLO、成本、備援；
- Model Owners：PIT、split、uncertainty、release。

在上述 production evidence 完成前，對外狀態為 `approved-for-parallel-implementation`，不是 `production-ready`。
