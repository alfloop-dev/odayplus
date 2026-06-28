---
doc_id: ODP-UXD-003-EXPANSION-WORKFLOW-BLUEPRINT
title: "ODay Plus Expansion Workflow Blueprint"
version: 0.1.0
status: draft
document_class: ux-blueprint
project: ODay Plus
language: zh-TW
updated_at: 2026-06-28
owner: "Product Design / Frontend"
approvers: "Product Lead / Frontend Lead"
content_format: markdown
source_documents:
  - docs/design/ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md
  - docs/design/ODAY_PLUS_R0_SCREEN_INVENTORY.md
  - docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md
  - docs/design/ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md
related_documents:
  - docs/design/ODAY_PLUS_HEATZONE_MAP_VISUAL_SPEC.md
  - docs/design/ODAY_PLUS_SITESCORE_REPORT_UI_SPEC.md
  - docs/data/ODAY_PLUS_CANONICAL_SCHEMA_BASELINE.md
---

# ODay Plus Expansion Workflow Blueprint

## 1. Purpose & Boundary

本文件定義 `expansion` workspace 內從熱區探勘到 SiteScore 核准的模組級 UX。它補齊 R0 殼層只提供插槽、不定義模組頁內容的缺口，讓前端 worker 不需自行發明 HeatZone、Listing、Candidate Site、SiteScore、開店審查的頁面行為。

範圍：

- **In scope**：`/w/expansion/heatzone`、`/w/expansion/listings`、`/w/expansion/candidates`、`/w/expansion/sitescore`、`/w/expansion/sitescore/:reportId` 的畫面任務、資訊層級、互動與狀態。
- **Out of scope**：R0 AppShell chrome、全域 Search/Tasks/Notifications、Admin、後端評分公式與資料清洗實作。
- **Source of truth**：元件 props/states 看 `ODAY_PLUS_COMPONENT_CONTRACTS.md`；地圖 layer 細節看 `ODAY_PLUS_HEATZONE_MAP_VISUAL_SPEC.md`；SiteScore report 細節看 `ODAY_PLUS_SITESCORE_REPORT_UI_SPEC.md`。

## 2. Expansion Decision Flow

```text
HeatZone Radar
  -> Listing Inbox
  -> Candidate Sites
  -> SiteScore Reports
  -> SiteScore Report Detail
  -> ApprovalPanel
  -> Decision Audit
```

每一步都必須是可分享 URL 狀態。使用者從地圖、列表、通知或任務中心切入時，返回後要保留原 filter、sort、tab、page、選中項與 drawer 狀態。

### 2.1 Decision Separation

畫面必須分離下列語意，不得混在同一個「AI 結論」區塊：

| Layer | Expansion 意義 | UI 呈現 |
|---|---|---|
| Prediction | HeatZone score、SiteScore P10/P50/P90、payback interval | Score card / forecast band / interval table |
| Recommendation | `GO|WAIT|REJECT|INVESTIGATE` 或高優先熱區 | Recommendation block，標示 model/policy/version |
| Human decision | 展店審查人的核准、退回、補件、override | `ApprovalPanel` + reason + risk acknowledgement |
| Execution | 建立開店任務、實勘、合約、開店準備 | Timeline / task links |
| Outcome | 開店後實際營收、回收期、模型校正 | Outcome slot；未成熟不得宣稱成效 |

## 3. Routes & Page Jobs

| Route | Page | Primary job | Default density | Main components |
|---|---|---|---|---|
| `/w/expansion/heatzone` | HeatZone Radar | 找出應優先探勘的商圈/格網 | comfortable | Map + `HeatZoneScoreCard` + Drawer |
| `/w/expansion/listings` | Listing Inbox | 檢查匯入、去重、硬規則失敗與可轉候選物件 | compact | `Table` + import summary + Drawer |
| `/w/expansion/candidates` | Candidate Sites | 比較候選點可行性並送 SiteScore | compact | `Table` + `CandidateSiteCard` + Drawer |
| `/w/expansion/sitescore` | SiteScore Reports | 掃描評分報告、待審狀態與模型新鮮度 | compact | `Table` + report preview Drawer |
| `/w/expansion/sitescore/:reportId` | SiteScore Report Detail | 做 GO/WAIT/REJECT/INVESTIGATE 審查 | comfortable | `SiteScoreReportSummary` + `EvidencePanel` + `ApprovalPanel` |

## 4. Shared Page Contract

所有 Expansion 模組頁都插入 R0 AppShell，且必備：

- Page Header：breadcrumb、title、summary、status badge、primary action、secondary actions、last updated。
- Filter Bar：filter/sort/tab/page/date range/selected entity 皆進 URL query。
- Content：loading、empty、error、permission 四態；不可只用 Toast 代表錯誤。
- Right Drawer：列表/地圖選中項快速查看；支援 deep link、上一筆/下一筆、Esc、focus trap。
- Data freshness：顯示 feature snapshot time、model version、source snapshot id 或 imported_at。
- Permission：無權限不顯示入口與操作；可讀不可寫顯示唯讀 badge；deep link 受限導 403。

## 5. HeatZone Radar Page

### 5.1 Page Header

- Title：`HeatZone Radar`
- Summary：`依需求缺口、ODay G2 Fit、租金可行性與 cannibalization risk 排序展店熱區。`
- Status：資料新鮮度 badge（`FRESH|STALE|PARTIAL|LOW_CONFIDENCE|FAILED_QA`）+ latest score job status。
- Primary action：`重新計算 HeatZone`，建立 `/heatzones/score-jobs` job；需 idempotency key；不顯示假進度百分比。
- Secondary actions：saved view、export visible rows、map theme、layer settings。

### 5.2 Content Layout

Desktop `lg+`：

```text
Filter Bar
Map canvas 70% width                         Ranked side panel 30%
  H3 layer / listing layer / competitor        top zones table
  selected zone tooltip                        HeatZoneScoreCard
```

Mobile `sm`：地圖改為只讀預覽 + 排名列表為主；完整地圖審查提示使用桌機。

### 5.3 Required Filters

- `district`
- `state=UNTOUCHED|PARTIALLY_ABSORBED|SATURATED|UNDER_REALIZED|STILL_EXPANDABLE|SUPPRESSED_LOW_CONFIDENCE`
- `scoreMin`
- `confidenceMin`
- `listingAvailability`
- `rentFeasibility`
- `modelVersion`
- `snapshot`

### 5.4 Select Zone Behavior

點擊 H3 cell 或排名列：

1. URL 加上 `?selected=<heat_zone_id>&drawer=zone`.
2. 開啟 drawer，顯示 `HeatZoneScoreCard`、score breakdown、warnings、source snapshots、附近 listings 與候選點。
3. Primary next action：`查看 Listing` 導到 `/w/expansion/listings?heatZone=<id>`。
4. 若 `confidence < 0.7` 或 state 為 `SUPPRESSED_LOW_CONFIDENCE`，禁止直接送 SiteScore，只允許建立資料補件/人工查核任務。

## 6. Listing Inbox Page

### 6.1 Page Header

- Title：`Listing 收件匣`
- Summary：`處理外部房源匯入、解析、去重、硬規則與候選點轉換。`
- Status：最近匯入 `imported_at`、source system、accepted/duplicate/rejected count。
- Primary action：`匯入 Listing`，走 `/listings/import-jobs`；匯入結果以 job/inline summary 顯示。

### 6.2 Table Columns

| Column | Required behavior |
|---|---|
| Listing | source name/id + address summary |
| Status | `RAW|PARSED|GEOCODED|DUPLICATE|FAILED_HARD_RULE|CANDIDATE` |
| Issue | issue code + field；硬規則失敗不可只顯示紅色 |
| Rent / Area | 敏感欄位依 field permission 遮罩 |
| Geocode | confidence + precision + h3 index |
| Duplicate | duplicate group + match strategy + confidence |
| HeatZone | linked `heat_zone_id` |
| Updated | imported_at / processed_at |
| Action | view, resolve duplicate, create candidate, request correction |

### 6.3 Row Drawer

Drawer 分區：

1. Source record：原始欄位與 field lineage。
2. Parsed canonical：Listing + AddressLocation 摘要。
3. Issues：validation、hard rule、duplicate、geocode warning。
4. Candidate conversion：可轉候選時顯示 `CandidateSiteCard` preview。
5. Audit：source snapshot、import job、actor、correlation id。

高風險或資料修正不可 optimistic；成功回傳 job_id 或 decision_id 後再更新列表。

## 7. Candidate Sites Page

### 7.1 Purpose

讓展店審查者比較已通過硬規則的候選點，確認地理編碼、租金、坪數、臨停/車流、heat zone fit，並送出 SiteScore。

### 7.2 Table Columns

| Column | Required behavior |
|---|---|
| Candidate | candidateSiteId + address |
| Status | `new/screened/scored/visited/rejected/approved/opened` 或 pipeline status |
| HeatZone | linked heat zone + score/state/confidence |
| Rent / Area / Frontage | sensitive mask by permission |
| Geocode | confidence + warning for low confidence |
| Feasibility | flags from hard rules and manual review |
| Listing Source | source system + imported_at |
| SiteScore | latest report recommendation/status |
| Action | open report, run score, request visit, reject |

### 7.3 Candidate Drawer

Drawer 顯示 `CandidateSiteCard`，並加上：

- HeatZone context：score breakdown、rank、warnings。
- Nearby evidence：competitor count、POI density、active listings、existing store count。
- Feasibility checklist：area/rent/floor/geocode hard rules and manual notes。
- Next actions：`執行 SiteScore`、`建立實勘任務`、`退回補件`。

若缺必要資料（address_id、h3_res_9、geocode confidence、rent、area），`執行 SiteScore` disabled 並以 tooltip 說明。

## 8. SiteScore Reports Page

列表頁用來掃描所有 candidate 的評分報告與審查狀態；detail 規格見 `ODAY_PLUS_SITESCORE_REPORT_UI_SPEC.md`。

### 8.1 Required Columns

| Column | Required behavior |
|---|---|
| Candidate | address + target format |
| Recommendation | `GO|WAIT|REJECT|INVESTIGATE` + reason summary |
| M1/M3/M6/M12 | interval summary；不可只顯 P50 |
| Payback | interval / warning |
| Confidence | level + reasons |
| Data freshness | feature snapshot time + stale warning |
| Decision | `DRAFT|SYSTEM_RECOMMENDED|PENDING_REVIEW|APPROVED|REJECTED|OVERRIDDEN|CLOSED` |
| Owner / SLA | reviewer and due time |
| Action | open report, submit review, compare candidates |

### 8.2 Preview Drawer

列表列點擊開 preview drawer，僅顯示摘要、top positive/negative factors、decision status 和 link：`開啟完整報告`。完整核准只在 detail page 或 Task Center drawer 內的完整 `ApprovalPanel` 執行。

## 9. Approval & Decision Rules

- SiteScore 核准是高風險動作，禁止 optimistic update。
- 必填 reason；override reason 最少 20 字；`riskAcknowledged=true`。
- 成功後顯示 `decision_id`、approval id、actor、timestamp、policy version、model version。
- `GO` 可建立後續開店任務；`WAIT` 必須指定 revisit date / missing evidence；`REJECT` 必須選原因；`INVESTIGATE` 必須建立補件或實勘任務。
- 審查人不能核准自己建立的 override；依後端權限與 segregation policy 決定。

## 10. Empty / Loading / Error / Permission

| Page | Empty state next action |
|---|---|
| HeatZone | `重新計算 HeatZone` 或 `檢查資料來源` |
| Listing Inbox | `匯入 Listing` |
| Candidate Sites | `從 Listing 建立候選點` |
| SiteScore Reports | `選擇候選點執行 SiteScore` |

錯誤狀態必含 code、correlation_id、retry、時間與下一步；資料部分失敗需區塊級 degraded，不使整頁空白。

## 11. Accessibility & Responsive

- 地圖頁必須有排名列表替代；色彩狀態需文字 + icon/pattern + tooltip。
- 表格支援 keyboard row focus、sorting `aria-sort`、drawer focus trap。
- `sm` 僅支援查看摘要、任務回覆與輕量核准；完整地圖與 SiteScore 審查需提示使用 `lg+`。
- 所有 detail breadcrumb：`展店 Expansion > Module > Entity`；mobile 收為返回上一層。

## 12. Implementation Directives

1. 不自創 route、狀態碼、顏色或 density；沿用 R0 design docs。
2. URL 是狀態來源；filter/sort/page/tab/selected/drawer 必須可還原。
3. Table/Drawer/ApprovalPanel/EvidencePanel 使用既有 component contracts。
4. Prediction、recommendation、human decision、execution、outcome 必須視覺分離。
5. Export、override、approval、sensitive field reveal 必須走 permission + audit。

## 13. Handoff Checklist

- [ ] HeatZone Radar 有 map + ranked list + drawer + data freshness + low confidence guard。
- [ ] Listing Inbox 覆蓋 import summary、RAW/DUPLICATE/FAILED_HARD_RULE/CANDIDATE、field lineage 與 issue drawer。
- [ ] Candidate Sites 覆蓋候選比較、HeatZone context、SiteScore readiness、disabled reason。
- [ ] SiteScore Reports 列表不可只顯 P50，需顯 intervals、confidence、decision status、staleness。
- [ ] Approval flow 禁 optimistic，提交後顯示 decision_id 並寫 Audit。
- [ ] 四態、權限、responsive、a11y、URL state 全部可逐條驗收。
