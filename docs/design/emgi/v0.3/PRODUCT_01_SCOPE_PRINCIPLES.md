---
doc_id: ODP-EMGI-PRODUCT-001-A
title: EMGI v0.3 Product — Scope and Principles
version: 0.3.0
status: approved-for-parallel-implementation
language: zh-TW
part: 1
part_count: 4
source_document: ODP-EMGI-PRODUCT-001@0.3.0
reviewed_baseline_sha: 0d1603cf347e30c9cf2f25f0eecc10673ac55015
updated_at: 2026-08-13
---

> Binding part 1 of 4. Read together with all parts listed in the EMGI package index.

# ODay Plus External Market & Geographic Intelligence 產品與資料產品定義

## 0. 文件定位

本文件定義 ODay Plus 的 **External Market & Geographic Intelligence（EMGI）** 產品、共同市場證據契約及對外發布資料產品。

本版本以實際展店、遷點、商圈監控、物件探勘、跨品牌分析及 SiteScore／HeatZone／NetPlan 的可重現決策需求為基準。補助計畫、現有 heuristic、既有欄位名稱或現行畫面均不得反向限制產品邊界。

本文件不是：

- 指定單一資料供應商的採購規格；
- SiteScore、HeatZone 或 NetPlan 的模型公式；
- 未經驗證就宣稱某項付費資料一定有效；
- 把所有外部資料塞入單一 PostgreSQL table 的設計；
- 將 crawler 技術研究等同正式 production publication；
- 把模型輸出直接當成投資核准；
- 用固定人類工期安排多 LLM 實作。

本文件是：

- 產品範圍、使用者與決策工作的上位定義；
- 市場證據、來源角色、時間、空間、缺值、負面證據與 readiness 的共同規則；
- Source Evidence、Canonical Observation、Decision-Ready Product 的資料產品目錄；
- SiteScore、HeatZone、Listing Radar、NetPlan、Survey 與 OpsBoard 的整合契約；
- 後續 SA、SD、API、event、schema、UI、來源研究及多 LLM 任務拆分的 binding baseline。

---

# 1. Executive Decision

## 1.1 一句話定義

**EMGI 是 ODay Plus 的市場證據與空間決策底座。**

它將人口、戶數、住宅、建物、POI、競店、供給容量、租金、物件、人流、OD、車流、路網、停車、重大建設、現場 Survey 與資料覆蓋，整理成可追溯、可比較、可重現、可增量更新的資料產品，支援：

1. 找出值得探勘的區域；
2. 理解任一地址及其真實服務圈；
3. 比較候選店址；
4. 監控競店、租金、物件與市場變化；
5. 指派最有價值的資料補件或 Survey；
6. 支援一代店升級、遷點、整併或退出；
7. 將跨品牌營運結果與外部市場環境作 point-in-time safe join；
8. 讓每一次模型與人工決策都能重建當時已知證據。

## 1.2 產品邊界

```text
modules/external_data
= 外部來源控制面
= provider / dataset / version / credential / retrieval /
  snapshot / observation / policy / cost / freshness /
  quarantine / lineage / publication grant

modules/market_intelligence
= 市場與地理 canonical domain
= geography / demographics / built environment / POI /
  competitors / mobility / traffic / parking / real estate /
  market events / survey / identity / data products

modules/site_feasibility
= 建物、公用設施、法定用途、施工與物理可開店性

modules/operating_context
= 天氣、假日、學期、費率、災害與營運外生環境

modules/sitescore / heatzone / netplan
= 只讀取 versioned decision-ready products
= 不直接抓外部 API 或網站
```

初期仍在 `odayplus` monorepo，不另開第三個 repo。當 EMGI 具獨立常設團隊、SLA、成本中心、多產品 consumer 或 release cadence 時，再評估抽出獨立平台。

## 1.3 管理母體與分析母體

| 角色 | 定義 | 允許用途 | 禁止用途 |
|---|---|---|---|
| `ODAY_G2_MANAGED` | ODay 二代店 | 完整市場監控、SiteScore、NetPlan、OpsBoard | 無 |
| `ODAY_G1_TRANSFORMATION_CANDIDATE` | 一代店升級／遷點／整併候選 | 原址診斷、新址比較、轉型專案 | 不預設進完整日常管理 |
| `CROSS_BRAND_ANALYTICAL_ONLY` | 其他品牌或非 ODay 店 | 模型訓練、匿名 benchmark、供給分析 | 未授權店級 UI、OpsBoard 任務、直接管理 |
| `EXTERNAL_REFERENCE` | 競店、POI、物件、建設 | 市場環境與供給證據 | 不視為自有管理據點 |

跨品牌資料是分析教材，不是管理範圍。`tenant_id` 可作 scope、audit、grouped split 與 leakage protection，不應直接作高基數模型 predictor。

---

# 2. Product Principles

## 2.1 決策先於來源

先定義：

- 使用者要做的決策；
- 需要的空間與時間粒度；
- 何種缺漏可降級；
- 何種缺漏必須 abstain 或建 Survey；
- 何種來源只作 discovery、verification、calibration 或 ground truth；
- 再選公開資料、商業資料、合作 feed、crawler、影像或人工觀測。

## 2.2 缺資料、零值與負面證據分離

每個 measurement 至少包含：

```text
value
availability_status
observation_count
coverage_ratio
freshness_status
uncertainty
source_manifest_id
```

`value = 0` 只有在下列條件成立時才合法：

```text
查詢或分區範圍明確
＋ source/search execution 完整
＋ 沒有截斷或飽和
＋ 來源健康
＋ observation count 可對帳
＋ negative_evidence_valid = true
```

否則必須是 `null`，並明列：

```text
PARTIAL
NOT_COLLECTED
NOT_LICENSED
NOT_AUTHORIZED
STALE
MISSING_UNEXPECTEDLY
QUARANTINED
SOURCE_ERROR
TRUNCATED
SATURATED
```

## 2.3 不使用單一 confidence 隱藏不同問題

共同輸出應依 domain 使用：

```text
identity_probability
state_probability
measurement_lower_bound
measurement_upper_bound
coverage_ratio
spatial_precision
temporal_precision
freshness_status
source_consistency
verification_status
```

例如「店是否同一實體」、「店是否仍營業」與「設備容量是多少」是不同的不確定性，不得平均成一個模糊分數。

## 2.4 Source Role 與 Source Dependency

每項來源明列角色：

```text
AUTHORITY
DISCOVERY
VERIFICATION
CALIBRATION
GROUND_TRUTH
FALLBACK
```

並保存 upstream dependency。若 All the Places、Overture、Foursquare 或其他資料集共享上游，不能把同一筆複製資料當成多個獨立證據。

## 2.5 技術可用性與政策狀態分離

來源狀態分成：

```text
technical_readiness:
  DISCOVERED
  SAMPLE_CAPTURED
  CONTRACT_VALIDATED
  CONNECTOR_REPLAYABLE
  BACKFILL_VERIFIED
  LIVE_VERIFIED
  PRODUCT_ACTIVE

policy_state:
  UNASSESSED
  WARNING
  OWNER_ACCEPTED
  OWNER_BLOCKED

publication_state:
  DISCOVERY_ONLY
  SHADOW_MODE
  ACTIVE_PRIMARY
  ACTIVE_FALLBACK
  DISABLED
```

Policy warning 不阻止技術 adapter、fixture、parser、replay、shadow evaluation；是否正式 publication 由 owner gate 決定。

## 2.6 Source Content 與 Observation 分離

同樣 bytes 可被多次觀測。系統必須分開：

```text
source_content_blob
source_observation
source_search_execution
```

Blob 可 content-addressed 去重；新的觀測仍更新 `last_seen_at`、狀態 persistence 與 evidence history。

## 2.7 時間語意

至少區分：

```text
event_time
effective_from / effective_to
source_published_at
observed_at
fetched_at
ingested_at
available_at
first_seen_at
last_seen_at
retracted_at
build_at
knowledge_as_of
effective_as_of
label_maturity_time
prediction_origin_time
```

模型 feature 必須滿足：

```text
available_at <= knowledge_as_of <= prediction_origin_time
```

Feature manifest 不得包含未來 label evidence。

## 2.8 Business Time

營運日、平假日、尖離峰及月界須依店點 `store_timezone` 與 `business_day_boundary` 決定。臺灣預設 `Asia/Taipei`，不得用 UTC 直接切 business date。

## 2.9 Source-native Geometry

```text
來源 geometry = 真實空間支撐
H3 = 索引、快取、聚合、相似市場檢索
Catchment = 真實服務範圍
Market Zone = 動態市場分析單元
行政區 = 報表、權限與對外對照
```

H3 不取代原始 polygon、road segment、building、parcel 或 point identity。

## 2.10 市場吸引力、物理可行性與經濟可行性分離

```text
Market Potential
≠ Physical Site Feasibility
≠ Unit Economics
≠ Investment Approval
```

市場分數高但法定用途、三相電、排水、通風或施工不可行的物件，不得進投資比較。模型分數可產生，但 decision readiness 不足時不得輸出 binding GO。

## 2.11 動態資料取得

不對每個候選點盲目查完所有付費來源。系統依 Value of Information 產生：

```text
data_gap_task
data_acquisition_plan
source_value_experiment
```

只補可能改變排名、風險或投資決策的資料。

## 2.12 每個結論可重現

SiteScore、HeatZone、NetPlan 或人工會議引用的資訊，必須綁定：

```text
effective_as_of
knowledge_as_of
target_format_snapshot_id
feature_policy_id
catchment_policy_id
component_manifest_ids
feature_source_manifest_id
model_version
decision_policy_version
```

資料更新不可改寫歷史決策依據。

## 2.13 EMGI 不輸出最終投資決策

EMGI 提供：

- 市場事實；
- 空間特徵；
- 變化訊號；
- readiness；
- coverage；
- evidence；
- uncertainty；
- acquisition recommendation。

EMGI 不直接輸出：

- GO／WAIT／REJECT；
- binding 回本期；
- 投資核准；
- 留守／遷點／退出最終決策。

---
