---
doc_id: ODP-UXD-004-OPERATIONS-ALERT-UI-SPEC
title: "ODay Plus Operations Alert UI Spec"
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
  - docs/design/ODAY_PLUS_OPSBOARD_SHELL_BLUEPRINT.md
  - docs/design/ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md
  - docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md
  - docs/design/ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md
  - docs/design/ODAY_PLUS_DESIGN_TOKENS.md
related_documents:
  - docs/design/ODAY_PLUS_INTERVENTION_WORKFLOW_UI_SPEC.md
  - modules/forecastops/domain/forecasting.py
  - docs_archive/05_module_design/ODP-MOD-04_FORECASTOPS.md
---

# ODay Plus Operations Alert UI Spec

## 1. Purpose & Boundary

本文件定義 `operations` workspace 內 ForecastOps 營運預警工作台的畫面任務、資訊層級、互動與狀態。它讓前端 worker 不需自行發明四燈、預測時序、根因證據與「預警 → 干預」交接的頁面行為，並讓 R3-002（ForecastOps 前端）可在無臨時視覺決策下實作。

範圍：

- **In scope**：`/w/operations/forecast`（預測總覽）、`/w/operations/alerts`（四燈預警佇列）、`/w/operations/forecast/:storeId`（單店預測與預警詳情）的畫面、互動、狀態與預警交接。
- **Out of scope**：R0 AppShell chrome、全域 Search/Tasks/Notifications、ForecastOps 模型公式與批次排程實作、干預生命週期內頁（見 `ODAY_PLUS_INTERVENTION_WORKFLOW_UI_SPEC.md`）。
- **Source of truth**：元件 props/states 看 `ODAY_PLUS_COMPONENT_CONTRACTS.md`；color/density token 看 `ODAY_PLUS_DESIGN_TOKENS.md`；後端字彙以 `modules/forecastops`（`AlertLevel`、`Alert`、`ForecastOutput`、`InterventionHandoff`）為準。

## 2. Backend Vocabulary (authoritative)

UI 必須沿用實作字彙，不得自創狀態或顏色：

| Concept | Backend source | Values / fields |
|---|---|---|
| 四燈等級 | `forecastops.AlertLevel` | `green` / `yellow` / `orange` / `red` |
| 預警 | `forecastops.Alert` | `alert_id`、`store_id`、`alert_level`、`alert_reason_code`、`evidence_json`、`opened_at`、`status`(open/closed)、`closed_at` |
| 預警原因碼 | `Alert.alert_reason_code` | `sitescore_gap` / `within_expected_band` |
| 預測輸出 | `forecastops.ForecastOutput` | `w4/w8/w12/w24`（各 `ForecastBand` p10/p50/p90）、`trajectory_class`、`turning_point_probability`、`sitescore_gap_ratio`、`actual_revenue`、`sitescore_baseline_p50` |
| 軌跡分類 | `ForecastOutput.trajectory_class` | `growing` / `ramping` / `plateau` / `declining` |
| 版本 | `ForecastOutput` | `model_version`、`feature_version`、`policy_version`（`four-light-policy-v1`）、`prediction_origin_time`、`scored_at`、`source_snapshot_ids` |
| 交接 | `forecastops.InterventionHandoff` | `handoff_id`、`alert_id`、`store_id`、`intervention_type`、`eligibility_status`、`action_set_json`、`status`(proposed) |

## 3. Decision Separation

畫面必須分離下列語意，不得混在同一個「AI 結論」區塊（沿用 visual system 五層）：

| Layer | Operations 意義 | UI 呈現 |
|---|---|---|
| Prediction | `ForecastOutput` 的 w4/w8/w12/w24 P10/P50/P90 預測帶、`turning_point_probability` | `ForecastBandChart` + interval table |
| Recommendation | 四燈等級與 `recommended_actions` | `FourLightBadge` + recommended action list，標示 `policy_version` |
| Human decision | 確認/關閉預警、決定是否建立干預 | Alert acknowledge + `送至干預` 入口 |
| Execution | 建立 `InterventionHandoff` 並導向 InterventionOps | Handoff card + 連結 |
| Outcome | 干預後實際營收回收、模型校正 | Outcome slot，於 InterventionOps 詳情頁，未成熟不得宣稱成效 |

## 4. Four-Light Policy (UI mapping)

四燈由 `sitescore_gap_ratio = (actual_revenue - sitescore_baseline_p50) / sitescore_baseline_p50` 推導（policy `four-light-policy-v1`）。UI 僅做呈現，門檻文字必須可見、不可只靠顏色：

| Light | Token | gap 條件 | 意義 | 是否交接干預 |
|---|---|---|---|---|
| `RED` | `color.status.red` | `gap <= -0.35` | 營收低於基準 ≥35%，需立即處理 | 是 → `maintenance`，`eligibility_status=manual_review` |
| `ORANGE` | `color.status.orange` | `-0.35 < gap <= -0.20` | 營收低於基準 20–35% | 是 → `promotion`，`eligibility_status=eligible` |
| `YELLOW` | `color.status.yellow` | `-0.20 < gap <= -0.10` | 營收低於基準 10–20%，僅提醒 | 否（alert-only） |
| `GREEN` | `color.status.green` | `gap > -0.10` | 在預期帶內 | 否（reason `within_expected_band`） |

`FourLightBadge` 必須：色塊 + 文字（如 `ORANGE`）+ icon/pattern + hover tooltip 顯示 gap 值與觸發門檻。色盲模式以 icon/pattern 區分。

## 5. Routes & Page Jobs

路由沿用 OpsBoard `/w/:workspace/:module[/:entityId]` 慣例。

| Route | Page | Primary job | Default density | Main components |
|---|---|---|---|---|
| `/w/operations/forecast` | Forecast 總覽 | 掃描各店預測軌跡、四燈分佈與模型新鮮度 | compact | `Table` + `FourLightBadge` + `DataStatusBadge` + Drawer |
| `/w/operations/alerts` | 四燈預警佇列 | 分流 RED/ORANGE/YELLOW 預警、確認與交接 | compact | `Table` + `AlertChip` + `FourLightBadge` + Drawer |
| `/w/operations/forecast/:storeId` | 單店預測 / 預警詳情 | 看懂預測帶、根因證據並決定是否建立干預 | comfortable | `ForecastBandChart` + `RootCauseEvidenceCard` + `AuditMetadata` |

## 6. Shared Page Contract

所有 Operations 頁插入 R0 AppShell，且必備：

- Page Header：breadcrumb（`營運 Operations > Module > Entity`）、title、summary、status badge、primary action、secondary actions、last updated（feature snapshot time + scored_at）。
- Filter Bar：`level`、`trajectory_class`、`district`、`modelVersion`、`snapshot`、date range、selected store 皆進 URL query。
- Content：loading、empty、error、permission 四態；錯誤不可只用 Toast。
- Right Drawer：列表選中項快速查看；支援 deep link、上一筆/下一筆、Esc、focus trap。
- Data freshness：`DataStatusBadge`（`FRESH|STALE|PARTIAL|MISSING|LOW_CONFIDENCE|FAILED_QA|BLOCKED`）+ `model_version` + `source_snapshot_ids`。
- Permission：無權限不顯示入口與操作；可讀不可寫顯示唯讀 badge；deep link 受限導 403。

## 7. Forecast 總覽 Page

### 7.1 Page Header

- Title：`Forecast 總覽`
- Summary：`依四燈等級與軌跡分類掃描各店營收預測與模型新鮮度。`
- Status：最近 forecast job 狀態（`QUEUED|RUNNING|SUCCEEDED|FAILED|PARTIAL|...`）+ 資料新鮮度。
- Primary action：`重新計算預測`，建立 `/forecastops/forecast-jobs` job；必帶 `Idempotency-Key`；不顯示假進度百分比。
- Secondary actions：saved view、export visible rows、切換 horizon（4w/8w/12w/24w）。

### 7.2 Table Columns

| Column | Required behavior |
|---|---|
| Store | `store_id` + 名稱 |
| Light | `FourLightBadge`（green/yellow/orange/red）+ gap 文字 |
| Trajectory | `trajectory_class`（growing/ramping/plateau/declining）+ icon |
| Forecast（選定 horizon） | P50 + P10–P90 區間；不可只顯 P50 |
| Gap vs baseline | `sitescore_gap_ratio` %，附 `sitescore_baseline_p50` |
| Turning point | `turning_point_probability` 機率徽章 |
| Freshness | feature snapshot time + stale warning |
| Model | `model_version` + `policy_version` |
| Action | 開啟詳情、查看預警、重算單店 |

### 7.3 Row Drawer

Drawer 顯示該店預測摘要、迷你 `ForecastBandChart`、目前四燈與最近預警，主要 next action：`開啟單店詳情`。

## 8. 四燈預警佇列 Page

### 8.1 Page Header

- Title：`四燈預警佇列`
- Summary：`分流 RED/ORANGE/YELLOW 營運預警、確認狀態並交接干預。`
- Status：各等級未處理 `open` 預警計數（RED/ORANGE/YELLOW）。
- Primary action：依選取列 `確認預警` 或 `建立干預`。
- Filters：`level`（對應 `GET /forecastops/alerts?level=`）、store、reason code、open/closed。

### 8.2 Table Columns

| Column | Required behavior |
|---|---|
| Alert | `alert_id` + `store_id` |
| Light | `FourLightBadge` + `alert_reason_code` |
| Evidence summary | 來自 `evidence_json`：actual vs forecast_p50、gap、trajectory |
| Opened | `opened_at` + 等待時間 |
| Status | `open` / `closed`（含 `closed_at`）；不可只用顏色 |
| Handoff | 若有 `InterventionHandoff`：`intervention_type` + `eligibility_status` |
| Action | acknowledge、建立干預、開啟單店詳情 |

### 8.3 Alert Acknowledge / Handoff Rules

- 確認與交接屬高風險動作，禁止 optimistic update；成功回傳 `handoff_id` 後再更新列表。
- 僅 RED/ORANGE 顯示 `建立干預`（後端只為這兩級產生 handoff）；YELLOW 只能 acknowledge 或建立資料查核任務。
- RED handoff `eligibility_status=manual_review`，UI 標示「需人工核准」；ORANGE `eligible` 仍須在 InterventionOps 走核准。
- 交接成功後顯示 `intervention_type`、`handoff_id`、`correlation_id`，並提供連結至 `/w/operations/interventions/:interventionId`。

## 9. 單店預測 / 預警詳情 Page

固定區段順序（沿用七層資訊層級，不得重排）：

1. **Summary**：`store_id`、目前四燈、gap、軌跡、`turning_point_probability`、需處理摘要。
2. **Status**：forecast job 狀態、`DataStatusBadge`、`model_version` / `policy_version`、SLA。
3. **Forecast**：`ForecastBandChart` —— 顯示 `actual_revenue`、forecast P50、P10–P90 帶、`sitescore_baseline_p50` 基準線，並標記 horizon（w4/w8/w12/w24）。圖表必附資料表替代與僅可見資料的 export。
4. **Root-cause Evidence**：`RootCauseEvidenceCard` —— 來自 `evidence_json` 的 actual vs forecast、gap、trajectory；列出 `recommended_actions`（RED：`inspect_machine_uptime` / `review_staffing` / `open_recovery_plan`；ORANGE：`launch_local_promotion` / `review_price_packaging` / `review_local_demand` / `create_intervention_candidate`）。正/負向訊號分區，低資料品質須在區段頂端警告，不可藏在 tooltip。
5. **Recommendation**：四燈與建議動作，明確標示「由系統依 `four-light-policy-v1` 產生」、`model_version`、`feature_version`、`prediction_origin_time`、需核准角色。
6. **Handoff / Execution**：若已交接顯示 `InterventionHandoff`（type、eligibility、`action_set_json` 摘要）與連結；未交接且符合條件顯示 `建立干預`。
7. **Version / Audit**：`AuditMetadata` —— `prediction_run_id`、`model_version`、`feature_version`、`policy_version`、`prediction_origin_time`、`scored_at`、`source_snapshot_ids`、actor、`correlation_id`。

可用 anchor tabs 快速跳轉，但本體順序不得重排。

## 10. High-Risk Action Rules

- 建立干預、確認 RED 預警、export 含敏感欄位皆為高風險：禁 optimistic、提交鎖定防重送、寫 backend Audit。
- 建立干預需 reason 捕捉與 evidence 可見；成功回傳 `handoff_id` / `intervention_id` 才更新 UI。
- 失敗保留表單值並顯示 code + `correlation_id` + 時間 + retry。

## 11. Empty / Loading / Error / Permission

| State | Required UI |
|---|---|
| Loading | header/table/chart skeleton；不顯示假四燈或假分數 |
| Empty | 尚無預測：action `重新計算預測`（具權限時） |
| Error | error summary + code + `correlation_id` + retry + timestamp；部分失敗用區塊級 degraded |
| Stale | 以 stale warning 呈現並提供 rerun |
| Permission | 唯讀或 403 依路由權限 |

## 12. Accessibility & Responsive

- 四燈/狀態一律 文字 + icon/pattern + tooltip；圖表須有資料表替代。
- 表格支援 keyboard row focus、`aria-sort`、drawer focus trap。
- `lg+`：完整預測帶 + 側欄摘要 + sticky action。
- `md`：單欄區段、底部 sticky action bar。
- `sm`：摘要、四燈、top evidence、任務回覆；完整模型審查提示 `完整模型審查請使用桌機版`。
- Density：列表 `compact`，詳情 `comfortable`；density 僅改間距字級，不改語意顏色。

## 13. Handoff Checklist

- [ ] 四燈用 `AlertLevel` 四值，badge 帶文字 + icon + tooltip，門檻可見。
- [ ] Forecast 表/圖顯示 w4/w8/w12/w24 P10/P50/P90，永不只顯 P50。
- [ ] 預警佇列覆蓋 open/closed、reason code、evidence summary 與 handoff 狀態。
- [ ] Root-cause evidence 顯示 actual vs forecast、gap、trajectory 與 `recommended_actions`，低品質置頂警告。
- [ ] 僅 RED/ORANGE 可建立干預；RED 標 `manual_review`；交接禁 optimistic 並回傳 `handoff_id`。
- [ ] Version/Audit 區呈現 model/feature/policy version、snapshot ids 與 correlation id。
- [ ] 四態、權限、responsive、a11y、URL state 全部可逐條驗收。
