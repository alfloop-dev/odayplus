---
doc_id: ODP-UXD-005-ASSET-AND-NETPLAN-UI-SPEC
title: "ODay Plus Asset and NetPlan UI Spec"
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
  - docs/design/ODAY_PLUS_LEARNING_HUB_UI_SPEC.md
  - docs/design/ODAY_PLUS_AUDIT_EVIDENCE_UI_SPEC.md
  - modules/avm/domain/valuation.py
  - modules/netplan/domain/planning.py
  - solver/netplan/optimizer.py
  - solver/netplan/model.py
  - docs_archive/05_module_design/ODP-MOD-08_DEALROOM_AVM.md
  - docs_archive/05_module_design/ODP-MOD-09_NETPLAN.md
---

# ODay Plus Asset and NetPlan UI Spec

## 1. Purpose & Boundary

本文件定義兩個高密度決策工作台的前端規格：`dealroom`（財務／交易）workspace 的 **DealRoomAVM 門市估值 + DataRoom**，與 `network`（網絡規劃）workspace 的 **NetPlan 店網情境規劃**。兩者都是低頻、高金額、需財務／策略核准的決策面，UI 必須讓資深決策者在一頁內掃描估值區間、安全動作集合與限制，並嚴格區分模型推薦與人工核准。本文件讓 R5-003 前端可在無臨時視覺決策下實作。

範圍：

- **In scope**：`/w/dealroom/cases`、`/w/dealroom/cases/:caseId`、`/w/network/scenarios`、`/w/network/scenarios/:scenarioId` 的畫面、互動、生命週期狀態、估值三鏡、DataRoom 清單與匯出、solver alternatives、infeasibility diagnosis、核准與結果觀察。
- **Out of scope**：R0 AppShell chrome（見 `ODAY_PLUS_OPSBOARD_SHELL_BLUEPRINT.md`）、估值演算法與 CP-SAT solver 實作、模型發布（見 `ODAY_PLUS_LEARNING_HUB_UI_SPEC.md`）、跨領域決策稽核與證據匯出格式（見 `ODAY_PLUS_AUDIT_EVIDENCE_UI_SPEC.md`）。
- **Source of truth**：元件 props/states 看 `ODAY_PLUS_COMPONENT_CONTRACTS.md`；AVM 字彙以 `modules/avm/domain/valuation.py` 為準；NetPlan 字彙以 `modules/netplan/domain/planning.py` 與 `solver/netplan/{model,optimizer}.py` 為準。導覽 key（`dealroom`、`network`）與 route 結構以 `ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md §3.2/§6` 為準，不得自創。

## 2. Decision Separation

兩個工作台都必須分離下列語意，不得混在同一個「AI 結論」區塊：

| Layer | Asset / NetPlan 意義 | UI 呈現 |
|---|---|---|
| Prediction | 估值三鏡的 P10/P50/P90、solver 候選的期望毛利 | `ValuationRangeChart` / interval、`NetPlanScenarioCard` 指標 |
| Recommendation | AVM fair/reserve/asking、solver 最佳計畫與 alternatives | 標示 `model_version`/`solver_version`，與人工核准視覺分離 |
| Human decision | 財務核准（reserve override）、情境核准/退回（+ reason） | `ApprovalPanel` |
| Execution | 建立 DataRoom 並匯出、執行店網行動 | DataRoom 區、execution 節點 |
| Outcome | 實際毛利 vs 期望、variance、結案 | Outcome 區；未觀察前不得宣稱成效 |

---

# Part A — DealRoomAVM 估值工作台（`dealroom`）

## A3. Backend Vocabulary (authoritative)

字彙以 `modules/avm/domain/valuation.py` 為準。

| Concept | Backend source | Values / fields |
|---|---|---|
| 案件狀態 | `ValuationCaseStatus` | `DRAFT`、`DATA_READY`、`NORMALIZING`、`VALUING`、`REVIEW_REQUIRED`、`APPROVED`、`DATAROOM_READY` |
| 估值輸入 | `ValuationInput` | `store_id`、`gm_ttm`、`forecast_gm_next_12m`、`asset_book_value`、`equipment_fair_value`、`lease_liability`、`working_capital`、`comparable_multiples`、`liquidity_discount`(0.1，界 0–0.5)、`quality_score`(0–1)、`source_snapshot_ids`、`prediction_origin_time` |
| 案件 | `ValuationCase` | `case_id`、`store_id`、`status`、`valuation_input`、`created_by`、`created_at`、`status_history` |
| 正規化毛利 | `NormalizedMargin` | `gm_ttm`、`gm_fwd`、`normalized_gm`（= 0.45·ttm + 0.55·fwd；`quality_score<0.8` 再 ×0.92）、`adjustment_reasons`、`confidence` |
| 估值鏡 | `LensValuation` | `lens`(`income`/`asset`/`market`/`blended`)、`p10`、`p50`、`p90`、`method`、`evidence` |
| 估值報告 | `ValuationReport` | `report_id`、`normalized_margin`、`lenses`、`fair_price`(P10/P50/P90 `PriceBand`)、`reserve_price`(= P10·0.97)、`asking_price`(= P90·1.05)、`confidence`、`model_version`、`feature_version`、`prediction_origin_time`、`valued_at`、`finance_approval`、`valuation_version` |
| 財務核准 | `ApprovalDecision` | `decision_id`、`actor_id`、`approved_at`、`decision_reason`(必填)、`reserve_price`(可覆寫)、`policy_version` |
| 資料室 | `DataRoom` / `DataRoomDocument` | `dataroom_id`、`checklist`(`financials`/`assets`/`lease`/`comparables`/`valuation_card`，各帶 `status`)、`valuation_card`、`export_audit` |
| 信心等級 | `_confidence(quality_score)` | `high`(≥0.9)、`medium`(≥0.75)、`low`(<0.75) |
| 版本 | constants | `model_version`(`dealroom-avm-baseline-v1`)、`feature_version`(`valuation-view-v1`)、`policy_version`(`avm-finance-approval-policy-v1`) |

估值鏡計算（呈現於 `evidence`，UI 須可展開）：`income` = `normalized_gm × 2.8`；`asset` = `book + equipment + working_capital − lease_liability`（下限 0）；`market` = `normalized_gm × comparable_multiple × (1 − liquidity_discount)`；`blended` 彙整三鏡為 `fair_price` 區間。

## A4. Case Lifecycle (UI)

```text
DATA_READY → NORMALIZING → DATA_READY
  → VALUING → REVIEW_REQUIRED
  → APPROVED → DATAROOM_READY (→ export)
```

- 服務方法：`create_case`(→`DATA_READY`)、`normalize`、`value`(→`REVIEW_REQUIRED`)、`approve_finance`(→`APPROVED`)、`build_dataroom`(→`DATAROOM_READY`)、`export_dataroom`。UI 以回傳的 `ValuationCase.status` 為準，不得樂觀跳狀態。
- `REVIEW_REQUIRED` 是核准前的決策閘門：未核准不得建立 DataRoom 或匯出。
- 每次轉移寫入 `status_history`（actor、reason、correlation_id），於 Audit 區呈現。

## A5. Routes & Page Jobs

| Route | Page | Primary job | Default density | Main components |
|---|---|---|---|---|
| `/w/dealroom/cases` | 估值案件列表 | 掃描案件狀態、信心、待核准與 DataRoom 就緒 | compact | `Table` + status badge + Drawer |
| `/w/dealroom/cases/:caseId` | 估值案件詳情 | 看三鏡估值區間、核准（含 reserve override）、建立並匯出 DataRoom | comfortable | `ValuationRangeChart` + `ApprovalPanel` + `EvidencePanel` + `AuditMetadata` |

## A6. 估值案件列表 Page

- Title：`估值案件`；Summary：`對門市做三鏡估值、財務核准並備妥交易資料室。`
- Primary action：`建立估值案件`（具權限時）。
- Filter Bar（皆進 URL query）：`store_id`、`status`、`confidence`、是否待核准、是否 DataRoom 就緒、date range、selected entity。
- Table columns：

| Column | Required behavior |
|---|---|
| Case | `case_id` + `store_id` |
| Status | `ValuationCaseStatus`（7 值）+ icon/pattern；terminal/就緒態以 token 區分，不只靠顏色 |
| Fair (P50) | `fair_price.p50`；敏感欄位依權限遮罩 |
| Reserve / Asking | `reserve_price` / `asking_price`；無權限遮罩並標示「受限」 |
| Confidence | `confidence`（high/medium/low）+ icon；low 須警示 |
| Finance approval | 未核准 / 已核准（actor + time） |
| DataRoom | 未建立 / 就緒 / 已匯出（次數） |
| Action | open、normalize、value、核准、建立 DataRoom、匯出 |

Drawer：顯示案件摘要、目前狀態、fair/reserve/asking（依權限）、信心與 next action `開啟案件詳情`。完整核准與匯出只在 detail 執行。

## A7. 估值案件詳情 Page

固定區段順序（不得重排）：

1. **Summary**：`store_id`、目前 `status`、`fair_price.p50`、`confidence`、是否已財務核准、DataRoom 狀態。
2. **Status & History**：狀態 + `status_history`（actor / reason / at / correlation_id）。
3. **Normalized Margin**：`NormalizedMargin` —— `gm_ttm`、`gm_fwd`、`normalized_gm` 與 `adjustment_reasons`（如 quality 折讓）；`confidence` 文字 + icon。
4. **Three-Lens Valuation**：`ValuationRangeChart`（component contracts §5.10）—— 對 `income`/`asset`/`market`/`blended` 各鏡顯示 P10/P50/P90 區間，`fair_price` 區間、`reserve_price` marker、`asking_price` marker、comparable markers。**永不只顯 P50**；每鏡可展開 `method` 與 `evidence`（multiple、liquidity_discount、lease_liability 等）。鏡間差異大時以 explicit comparison 呈現（並排區間條 + 差額），讓決策者一眼看出分歧來源。
5. **Approval (Finance)**：見 A8。
6. **DataRoom**：見 A9。
7. **Version / Audit**：`AuditMetadata`（§4.16）—— `model_version`、`feature_version`、`policy_version`、`prediction_origin_time`、`valued_at`、actor、reason、correlation_id。

`ValuationRangeChart` 另含 `liquidityScore`、`dataRoomCompleteness`、`financeApprovalStatus`；reserve/asking 為敏感欄位，依權限遮罩並限制匯出。

## A8. Approval Rules (Finance)

`ApprovalPanel`（高風險，禁 optimistic）：

- 必須先到 `REVIEW_REQUIRED` 才能核准；必填 `decision_reason`；提交鎖定防重送。
- 系統 `fair/reserve/asking` 與人工核准視覺分離，標示「由 AVM 模型產生」與 `model_version`。
- 允許 **reserve price override**：覆寫時必填 reason，明確標示原始 `reserve_price`(P10·0.97) 與覆寫值之差。
- 成功顯示 `decision_id`、`actor_id`、`approved_at`、`policy_version`(`avm-finance-approval-policy-v1`)、correlation_id；狀態 → `APPROVED`。
- Segregation：建立者不得核准自己的案，除非 policy 允許；唯讀使用者無動作鈕、reserve/asking 遮罩。
- `confidence=low` 或資料異常時，核准鈕仍可用但須顯眼提示風險並要求 reason 說明。

## A9. DataRoom & Export Section

DataRoom 是交易資料的可匯出證據包，匯出本身為高風險審計動作：

- 僅 `APPROVED` 後可 `build_dataroom`；建立後 `status=DATAROOM_READY`。
- 顯示 `checklist`：`financials`、`assets`、`lease`、`comparables`、`valuation_card`，各帶 `status`（ready/缺件）；缺件時不得宣稱資料室完整。
- 顯示 `valuation_card`（fair/reserve/asking 摘要）與 `dataRoomCompleteness`。
- **匯出**（`export_dataroom`）：必填 reason、禁 optimistic、寫後端 Audit（`avm.dataroom_exported.v1`）；成功後追加 `export_audit`（actor / reason / exported_at / correlation_id）並於區內列出歷次匯出。匯出格式與證據鏈見 `ODAY_PLUS_AUDIT_EVIDENCE_UI_SPEC.md`。
- 敏感數值（reserve/asking、PII）依權限遮罩；無匯出權限者隱藏匯出入口。

---

# Part B — NetPlan 店網情境工作台（`network`）

## B3. Backend Vocabulary (authoritative)

字彙以 `modules/netplan/domain/planning.py` 與 `solver/netplan/{model,optimizer}.py` 為準。

| Concept | Backend source | Values / fields |
|---|---|---|
| 情境狀態 | `NetPlanScenarioStatus` | `draft`、`solved`、`infeasible`、`pending_approval`、`approved`、`rejected`、`executed`、`outcome_observed`、`closed` |
| 合法轉移 | `VALID_TRANSITIONS` | draft→{solved,infeasible}；solved→{pending_approval,rejected}；pending_approval→{approved,rejected}；approved→executed；executed→outcome_observed；outcome_observed→closed；infeasible/rejected/closed 無出邊 |
| 既有門市輸入 | `ExistingStoreInput` | `store_id`、`baseline_gross_margin`、improve/move 的 `*_gross_margin_uplift`/`*_cost`、`exit_cost`、各動作 `*_risk`(keep 0.1/improve 0.25/move 0.35/exit 0.2)、`current_capacity`、`source_snapshot_ids` |
| 候選點位輸入 | `CandidateSiteInput` | `candidate_site_id`、`expected_gross_margin`、`open_cost`、`risk_score`、`capacity_delta`、`source_snapshot_ids` |
| 限制 | `NetPlanConstraints` | `max_budget`、`min_expected_gross_margin?`、`min_capacity_delta?`、`max_average_risk?`、`min_action_counts`/`max_action_counts`（依動作）、`policy_version` |
| 動作 | `NetworkAction` | `OPEN`、`KEEP`、`IMPROVE`、`MOVE`、`EXIT` |
| 動作選項 | `ActionOption` | `entity_id`、`action`、`expected_gross_margin`、`budget_cost`、`risk_score`、`capacity_delta`、`source_snapshot_ids`、`notes` |
| 情境 | `NetPlanScenario` | `scenario_id`、`tenant_id`、`scenario_name`、`planning_horizon`、`options_by_entity`、`constraints`、`status`、`status_history`、`model_version`、`feature_version`、`solver_version` |
| 解算結果 | `NetworkPlanSolveResult` | `solver_status`(optimal/feasible/infeasible)、`objective_value`、`selected_actions`、`expected_gross_margin`、`budget_usage`、`average_risk`、`capacity_delta`、`action_counts`、`binding_constraints`、`alternatives`、`infeasible`、`diagnostics`、`solver_version` |
| 不可行診斷 | `InfeasibilityDiagnosis` | `violated_constraint`、`affected_stores`、`required_relaxation`、`business_impact`、`suggested_action` |
| 核准 | `ApprovalRecord` | `approval_id`、`actor_id`、`decision`(approved/rejected)、`reason`、`decided_at`、`policy_version` |
| 執行 | `ExecutionRecord` | `execution_id`、`actions`、`executed_by`、`executed_at` |
| 結果 | `OutcomeRecord` | `expected_gross_margin`、`actual_gross_margin`、`variance`、`variance_pct`、`observed_at`、`source_snapshot_ids`、`label_registry_payload` |
| 版本 | constants | `model_version`(`netplan-network-baseline-v1`)、`feature_version`(`network-plan-view-v1`)、`solver_version`(`netplan-exhaustive-cpsat-compatible-v1`)、`policy_version`(`netplan-network-policy-v1`) |

## B4. Scenario Lifecycle (UI)

```text
draft → solved | infeasible
solved → pending_approval | rejected
pending_approval → approved | rejected
approved → executed → outcome_observed → closed
```

- 服務方法：`solve`、`submit_for_approval`、`decide`、`execute`、`outcome`(記 variance)、`close`。UI 只依 `VALID_TRANSITIONS` 呈現可用動作，並以回傳 `status` 為準。
- Terminal（灰化動作）：`infeasible`、`rejected`、`closed`。
- `infeasible` 不可前進，UI 只呈現 diagnosis 與「修改情境」入口（建立新 draft），**不得在前端自動放寬限制**。

## B5. Routes & Page Jobs

| Route | Page | Primary job | Default density | Main components |
|---|---|---|---|---|
| `/w/network/scenarios` | 情境列表 | 掃描情境狀態、solver 結果、待核准與不可行 | compact | `Table` + status badge + Drawer |
| `/w/network/scenarios/:scenarioId` | 情境詳情 | 設定限制、解算、看最佳計畫 + alternatives / infeasibility、核准、執行、結果 | comfortable | `NetPlanScenarioCard` + `ApprovalPanel` + `AuditMetadata` |

### B5.1 情境列表 Table Columns

| Column | Required behavior |
|---|---|
| Scenario | `scenario_id` + `scenario_name` + `planning_horizon` |
| Status | `NetPlanScenarioStatus`（9 值）+ icon/pattern；`infeasible` 以 orange/red token 警示 |
| Solver | `solver_status`（optimal/feasible/infeasible） |
| Objective | `objective_value`（= 期望毛利 − 風險懲罰） |
| Actions | `action_counts`（OPEN/KEEP/IMPROVE/MOVE/EXIT 計數） |
| Budget | `budget_usage` vs `max_budget` |
| Risk | `average_risk` vs `max_average_risk` |
| Approval | 未送審 / 待核准 / 已核准 / 退回 |
| Action | open、solve、送審、核准、執行、記錄結果、結案 |

## B6. 情境詳情 Page

固定區段順序（不得重排）：

1. **Summary**：`scenario_name`、`planning_horizon`、目前 `status`、`solver_status`、`objective_value`、`budget_usage`、`average_risk`。
2. **Status & History**：狀態 + `status_history`（`StatusTransition`：from/to/actor/reason/at/correlation_id）。
3. **Scenario Builder（Constraints & Options）**：呈現 `NetPlanConstraints`（`max_budget`、`min_expected_gross_margin`、`min_capacity_delta`、`max_average_risk`、min/max action counts）與每 entity 的 `options_by_entity`（各 `ActionOption`：action、期望毛利、`budget_cost`、`risk_score`、`capacity_delta`、notes）。限制編輯後須重新 `solve`，UI 不得用舊解。
4. **Solve Result**：見 B7（feasible）/ B8（infeasible）。
5. **Approval**：見 B9。
6. **Execution & Outcome**：見 B10。
7. **Version / Audit**：`AuditMetadata` —— `model_version`、`feature_version`、`solver_version`、`policy_version`、actor、reason、correlation_id。

## B7. Solve Result — Feasible（`NetPlanScenarioCard`）

`NetPlanScenarioCard`（component contracts §5.11）呈現最佳計畫：

- 欄位：`scenarioName`、`objectiveValue`、`OPEN/KEEP/IMPROVE/MOVE/EXIT count`、`budgetUsage`、`expectedGM`、`risk`、`bindingConstraints`、`solverStatus`、`alternativePlanAvailable`、`approvalStatus`。
- **Selected actions**：列出 `selected_actions`（每 entity 的 action + 期望毛利 + 成本 + 風險 + capacity_delta + notes）。
- **Binding constraints**：以 tag 呈現綁定限制（如 `max_budget` 100%、`max_average_risk` 95%），讓決策者知道哪條限制在「卡住」目標。
- **Alternatives**：列出 `alternatives`（top-N，去重 action signature）；每個 alternative 以 explicit comparison 與最佳計畫並排：Δobjective、Δbudget、Δrisk、動作差異 highlight。大型 solver 不顯示假進度百分比（只顯示「解算中」狀態）。
- 解算結果為模型產物，標示 `solver_version`；不得呈現為已核准。

## B8. Solve Result — Infeasibility Diagnosis

當 `solver_status=infeasible`（`infeasible=true`），以 diagnosis 面板取代計畫卡，**UI 不自動放寬任何限制**：

- 逐條呈現 `diagnostics`（`InfeasibilityDiagnosis`）：`violated_constraint`（標題）、`affected_stores`（清單，連結各 entity）、`required_relaxation`（需放寬多少）、`business_impact`（白話影響）、`suggested_action`（建議下一步）。
- 以警示樣式（orange/red token + icon + 文字，不只顏色）標示；提供「修改情境」入口回到 Scenario Builder 建立新 draft。
- 狀態 `infeasible` 為 terminal：不顯示送審/核准動作。

## B9. Approval Rules (NetPlan)

`ApprovalPanel`（高風險，禁 optimistic）：

- 必須先 `solved → pending_approval`（`submit_for_approval`）才能核准；`decide` 必填 `reason`，approved/rejected 皆然；提交鎖定防重送。
- 系統最佳計畫與 alternatives 與人工決策視覺分離，標示 `solver_version`/`model_version`。
- 成功顯示 `approval_id`、`actor_id`、`decision`、`decided_at`、`policy_version`(`netplan-network-policy-v1`)、correlation_id。
- Segregation：建立者不得核准自己的情境，除非 policy 允許；唯讀使用者無動作鈕。
- `infeasible` 或仍 `draft` 時核准鈕不顯示。

## B10. Execution & Outcome Section

- `approved → executed`（`execute`）採用最新 solve 的 `selected_actions`；顯示 `ExecutionRecord`（actions、executed_by、executed_at）。
- `executed → outcome_observed`（`outcome`）記錄 `actual_gross_margin`，後端計算 `variance` / `variance_pct` 並寫 label registry；UI 以 explicit comparison 呈現 `expected_gross_margin` vs `actual_gross_margin`（差額 + 百分比 + 方向 icon）。
- 結果觀察前（`executed`）不得宣稱成效；僅顯示「待觀察結果」。
- `outcome_observed → closed`（`close`）為終態，保留完整歷程與 Audit。

---

## 3. Cross-Cutting Rules (Asset & NetPlan)

### 3.1 Empty / Loading / Error / Permission

| State | Required UI |
|---|---|
| Loading | section/chart skeleton；不顯示假估值、假計畫、假 objective |
| Empty | 尚無案件/情境：建立入口（具權限時） |
| Error | error summary + code + `correlation_id` + retry + timestamp；部分失敗區塊級 degraded |
| Permission | 唯讀或 403 依路由與 segregation；reserve/asking 等敏感值遮罩 |

### 3.2 High-Risk Action Rules

- 財務核准、reserve override、DataRoom 匯出、情境核准/退回、執行皆禁 optimistic、必填 reason、寫後端 Audit。
- 成功才更新 UI 並顯示 `decision_id`/`approval_id`/`dataroom_id` + correlation_id；失敗保留表單值。
- `infeasible`（NetPlan）阻擋送審/核准；`confidence=low`（AVM）須顯眼提示但不阻擋。

### 3.3 Dense Executive Views & Comparison

- 列表預設 `compact`，詳情 `comfortable`，executive/wall screen 可切 `presentation` 密度（不改語意色與資訊層級，見 design tokens §9）。
- 估值三鏡、solver alternatives、expected vs actual 一律以 **explicit comparison**（並排區間/數值 + 差額 + 方向）呈現，不要求決策者心算。
- 區間值（fair/reserve/asking、objective、variance）一律配資料表替代與僅可見資料 export。

### 3.4 Accessibility & Responsive

- 所有狀態/信心/風險/不可行皆 文字 + icon/pattern + tooltip；圖表須有資料表替代。
- 表格支援 keyboard row focus、`aria-sort`、drawer focus trap。
- `lg+`：完整三鏡圖 / 計畫卡 + alternatives + sticky `ApprovalPanel`；`md`：單欄 + 底部 action bar；`sm`：摘要與輕量核准，完整審查提示桌機。
- Density 不改語意顏色。

## 4. Handoff Checklist

- [ ] AVM 用 `ValuationCaseStatus` 7 值與服務方法流程；`REVIEW_REQUIRED` 前不得建 DataRoom 或匯出。
- [ ] `ValuationRangeChart` 顯示 income/asset/market/blended 四鏡 P10/P50/P90 與 reserve/asking marker，永不只顯 P50，鏡間差異以 explicit comparison 呈現。
- [ ] 財務核准禁 optimistic、必填 reason、支援 reserve override（標示與原值差）、segregation，成功顯示 decision_id。
- [ ] DataRoom 顯示 5 項 checklist 狀態與 `valuation_card`；匯出必填 reason、寫 Audit、追加 `export_audit`，敏感值遮罩。
- [ ] NetPlan 用 `NetPlanScenarioStatus` 9 值與 `VALID_TRANSITIONS`；`infeasible`/`rejected`/`closed` 為 terminal。
- [ ] `NetPlanScenarioCard` 顯示 action_counts、budget/risk usage、binding constraints、alternatives（並排比較）；solver 無假進度。
- [ ] Infeasibility 逐條顯示 violated_constraint/affected_stores/required_relaxation/business_impact/suggested_action；UI 不自動放寬限制。
- [ ] 情境核准禁 optimistic、必填 reason、segregation；執行用最新 solve；結果以 expected vs actual + variance 呈現。
- [ ] 四態、權限、responsive、a11y、URL state、`presentation` 密度全部可逐條驗收。
