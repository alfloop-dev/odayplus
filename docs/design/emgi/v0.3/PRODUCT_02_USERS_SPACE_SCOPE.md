---
doc_id: ODP-EMGI-PRODUCT-001-B
title: EMGI v0.3 Product — Users, Space and Scope
version: 0.3.0
status: approved-for-parallel-implementation
language: zh-TW
part: 2
part_count: 4
source_document: ODP-EMGI-PRODUCT-001@0.3.0
reviewed_baseline_sha: 0d1603cf347e30c9cf2f25f0eecc10673ac55015
updated_at: 2026-08-13
---

> Binding part 2 of 4. Read together with all parts listed in the EMGI package index.

# 3. Users and Decision Jobs

## 3.1 使用者

| 使用者 | 主要工作 |
|---|---|
| Expansion Strategist | 找區域、排序市場、設定探勘優先序 |
| Site Analyst | 建立地址 dossier、比較候選點、提出補件 |
| Regional Operations | 一代店升級、遷點與市場變化判斷 |
| Field Surveyor | 現勘、照片、競店容量、臨停、入口與公用條件 |
| Data Steward | identity、taxonomy、quarantine、來源與修正 |
| GIS / Data Analyst | routing、catchment、H3、空間品質與 market zone |
| Data Partnership / Legal | license、purpose、export、policy state |
| Model Engineer | 消費 immutable data products，不讀 raw provider |
| Governance Reviewer | 核准高風險修正與 publication |
| Finance / Development | CAPEX、OPEX、租金、回本與工程條件 |

## 3.2 核心工作

### J1 找值得探勘的區域

輸出：

- demand surface；
- unmet demand；
- competitor／own capacity；
- rent feasibility；
- listing opportunity；
- physical constraints；
- data coverage；
- 建議下一個 data acquisition action。

### J2 理解任一地址

輸出：

- identity／geocode；
- WALK／SCOOTER_ESTIMATED／CAR catchments；
- demographics／housing；
- POI composition；
- competitor supply；
- rent／listing；
- accessibility／parking；
- market events；
- physical feasibility；
- Survey gaps；
- evidence lineage。

### J3 同版本比較候選點

比較必須固定：

```text
knowledge_as_of
effective_as_of
target_format_snapshot
feature policy
catchment policy
source version policy
readiness level
```

### J4 監控競店與市場供給

輸出：

- 新開候選；
- POSSIBLY／LIKELY／VERIFIED closed；
- 容量、價格、營業時間變化；
- 自家服務圈重疊；
- 需人工 review 的低信心狀態。

### J5 找活躍物件

輸出：

- active／price changed／possibly removed／removed／relisted；
- rent percentile；
- property identity；
- physical feasibility fields；
- source persistence；
- 是否可轉 Candidate。

### J6 一代店升級／遷點／整併

同時比較：

- 原址 market potential；
- 原址 actual performance；
- 市場變化；
- 新址；
- cannibalization；
- physical feasibility；
- economics；
- execution timing。

### J7 解釋門市差異

以 point-in-time safe join 回答：

- 相似設備規模為何營收不同；
- 競店進入前後；
- 人口、POI、租金、道路與活動變化；
- 哪些是 observed、estimated 或 missing。

### J8 規劃資料補件

輸出：

- 最可能改變決策的缺口；
- 來源費用與預期不確定性下降；
- 是否呼叫商業 API、crawler、routing、影像或 Survey；
- quota 與時間成本。

---

# 4. Spatial and Temporal Model

## 4.1 空間層級

| 層級 | 用途 |
|---|---|
| Point | 店、候選、POI、競店、listing、sensor |
| Address / Building / Parcel | 法定與物理 identity |
| Road Segment | 路況、車流、路權、map matching |
| Source Polygon | 村里、統計區、都市計畫、災害 |
| H3 R10 | 店門口與高密度鄰里 cache |
| H3 R9 | 一般市場 profile 預設索引 |
| H3 R8 | 城市初掃 |
| Catchment | WALK／SCOOTER_ESTIMATED／CAR 服務圈 |
| Market Zone | 依需求、供給、路網與服務圈衍生 |
| Administrative Area | 權限、報表與來源對照 |

## 4.2 Catchment

每份 catchment 保存：

```text
catchment_snapshot_id
origin_address_id
travel_mode
duration_minutes
routing_provider
routing_engine_version
road_graph_snapshot_id
traffic_assumption
generated_at
geometry
h3_cover
quality_flags
```

`SCOOTER` 在臺灣路權 benchmark 完成前標示為 `SCOOTER_ESTIMATED`。

## 4.3 兩種 As-Of

Build request 必須分開：

```text
effective_as_of
= 評估現實世界哪個時間點

knowledge_as_of
= 只允許使用何時之前已知的資料
```

---

# 5. Scope, Sharing and Purpose

原單一 Dataset Scope 改為四軸：

```text
owner_scope:
  PLATFORM
  PARTNER
  TENANT

sharing_scope:
  GLOBAL
  AUTHORIZED_CONSUMERS
  SINGLE_TENANT

sensitivity_class:
  PUBLIC
  LICENSED
  PRIVATE
  RESTRICTED

purpose_grants:
  TRAIN_MODEL
  BUILD_BENCHMARK
  SCORE_SITE
  COMPARE_CANDIDATES
  MONITOR_MARKET
  MANAGE_ODAY_STORE
  DISPLAY_RAW
  DISPLAY_DERIVED
  EXPORT_RAW
  EXPORT_DERIVED
  AUDIT_EVIDENCE
```

Ingestion scope 由 dataset registry 與 principal grant 決定，不接受 caller 任意指定假 tenant。

---
