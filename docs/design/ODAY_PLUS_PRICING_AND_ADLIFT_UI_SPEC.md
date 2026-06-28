---
doc_id: ODP-UXD-004-PRICING-AND-ADLIFT-UI-SPEC
title: "ODay Plus Pricing and AdLift UI Spec"
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
  - modules/priceops/domain/pricing.py
  - docs_archive/05_module_design/ODP-MOD-06_PRICEOPS.md
  - docs_archive/05_module_design/ODP-MOD-07_ADLIFT.md
---

# ODay Plus Pricing and AdLift UI Spec

## 1. Purpose & Boundary

本文件定義 `pricing` workspace 內兩個高風險工作台：PriceOps 調價（safe action set、模擬、最佳化、核准、rollback）與 AdLift 廣告增量分析（campaign workflow、matched controls、pre-trend、incrementality report）。它讓前端 worker 不需自行發明調價守則或增量報告狀態，並讓 R4-002/R4-003 前端可在無臨時視覺決策下實作。

範圍：

- **In scope**：`/w/pricing/priceplans`、`/w/pricing/priceplans/:planId`、`/w/pricing/adlift`、`/w/pricing/adlift/:campaignId` 的畫面、互動、狀態、核准與 rollback。
- **Out of scope**：R0 AppShell chrome、定價彈性/需求模型與 DiD/合成控制實作、共用干預生命週期內頁（核准/觀察/評估的共通行為見 `ODAY_PLUS_INTERVENTION_WORKFLOW_UI_SPEC.md`）。
- **Source of truth**：PriceOps 字彙以 `modules/priceops/domain/pricing.py` 為準；AdLift 字彙以 `modules/adlift/domain/incrementality.py`（已實作 DiD 增量分析）為準，campaign 的核准/觀察/評估生命週期透過共用 `InterventionOps`（`InterventionKind.AD_CAMPAIGN`，`campaign_intervention_id` 連結）。報告卡片沿用 `AdLiftReportCard` contract（component contracts §5.9）。

## 2. Decision Separation

PriceOps 與 AdLift 都必須分離下列語意，不得混在同一個「AI 結論」區塊：

| Layer | Pricing / AdLift 意義 | UI 呈現 |
|---|---|---|
| Prediction | 調價需求模擬帶（demand/revenue/gross_margin P10/P50/P90）、廣告 lift 估計 | `ForecastBandChart` / interval table |
| Recommendation | optimizer 推薦價、`safe_action_set`；廣告 `recommendation` | `PricingPlanComparison`、標示 solver/model version |
| Human decision | 核准/退回（`ApprovalRecord` + reason） | `ApprovalPanel` |
| Execution | 套用價格 / 啟動 campaign，開啟觀察窗 | execution + observation 節點 |
| Outcome | 實際 incremental gross margin、rollback 建議、lift 顯著性 | Outcome / Effect 區；未成熟不得宣稱成效 |

---

# Part A — PriceOps 調價工作台

## A3. Backend Vocabulary (authoritative)

| Concept | Backend source | Values / fields |
|---|---|---|
| 計畫狀態 | `PlanStatus` | `candidate`、`simulated`、`optimized`、`pending_approval`、`approved_for_pilot`、`active`、`observing`、`evaluated`、`continue`、`adjust`、`stop`、`rollback` |
| 計畫 | `PricingPlan` | `plan_id`、`tenant_id`、`status`、`items`、`created_at`、`correlation_id`、`status_history` |
| Safe action 守則 | `PriceConstraints` | `unit_cost`、`current_price`、`margin_floor_ratio`(0.15)、`max_increase_pct`(0.15)、`max_decrease_pct`(0.15)、`price_ladder_step`(0.5)、`min_price`、`max_price`、`policy_version`(`brand-pricing-policy-v1`) |
| 違規碼 | constraint violations | `margin_floor`、`max_increase_exceeded`、`max_decrease_exceeded`、`below_min_price`、`above_max_price`、`off_price_ladder` |
| 模擬 | `SimulationResult` (Band) | `price`、`demand`/`revenue`/`gross_margin` 各 p10/p50/p90 |
| 最佳化 | `OptimizationResult` | `recommended_price`、`safe_action_set`、`incremental_gross_margin`、`risk_level`、`requires_approval`、`binding_constraints`、`constraint_violations`、`solver_status`、`solver_version`(`priceops-exhaustive-ladder-v1`) |
| 計畫級最佳化 | `PlanOptimization` | `total_incremental_gross_margin`、`hard_constraint_violation_count`(必須 0)、`is_constraint_safe` |
| 核准 | `ApprovalRecord` | `decision_id`、`actor_id`、`decision`(approved/rejected)、`decision_reason`、`approved_at`、`policy_version` |
| Rollback | `RollbackPlan` / `RollbackRecommendation` | `rollback_plan_id`、`reverts`(item→`revert_to_price`)、`trigger_conditions`；`recommended`、`reason_code`、`impact_ratio`、`threshold` |
| 效果評估 | `PricingEffectEvaluation` | `baseline/expected/actual_incremental_gross_margin`、`impact_ratio`、`evidence_level`、`recommended_next_status` |
| 轉移審計 | `StatusTransition` | `from_status`、`to_status`、`actor`、`reason`、`occurred_at`、`correlation_id` |

## A4. Plan Lifecycle (UI)

```text
candidate → simulated → optimized → pending_approval
  → approved_for_pilot → active → observing → evaluated
  → continue | adjust | stop | rollback
```

- Terminal：`continue`、`stop`、`rollback`（`adjust` 回到 `simulated` 重新最佳化）。
- 每個狀態經 `simulate / optimize / submit / approve / activate / start_observation / evaluate / rollback` 服務方法推進；UI 以回傳 `PricingPlan.status` 為準，不得樂觀跳狀態。
- 啟動（activate）前必須已有 `RollbackPlan`（後端 `MissingRollbackPlanError` 強制）；UI 在無 rollback plan 時停用 `啟動` 並說明。

## A5. Routes & Page Jobs

| Route | Page | Primary job | Default density | Main components |
|---|---|---|---|---|
| `/w/pricing/priceplans` | 調價計畫列表 | 掃描計畫狀態、風險、待審與觀察窗 | compact | `Table` + status badge + Drawer |
| `/w/pricing/priceplans/:planId` | 調價計畫詳情 | 看模擬/最佳化、核准、啟動、觀察、評估、rollback | comfortable | `PricingPlanComparison` + `ApprovalPanel` + `AuditMetadata` |

## A6. 調價計畫列表 Page

- Title：`調價計畫`；Summary：`在 safe action set 內模擬、最佳化並核准門市調價，保留 rollback。`
- Primary action：`建立調價計畫`。
- Table columns：

| Column | Required behavior |
|---|---|
| Plan | `plan_id` + tenant |
| Status | `PlanStatus`（12 值）+ icon/pattern；terminal 灰化 |
| Items | 店/機台數 |
| Expected ΔGM | `total_incremental_gross_margin` |
| Risk | `risk_level`（low/medium/high）+ `requires_approval` 標示 |
| Constraints | `hard_constraint_violation_count`；非 0 須顯眼警示 |
| Observation | 觀察窗開啟/到期/是否成熟 |
| Action | open、simulate、optimize、submit、approve、activate、evaluate、rollback |

## A7. 調價計畫詳情 Page

固定區段順序（不得重排）：

1. **Summary**：plan、tenant、目前 `status`、預期 ΔGM、風險、需核准角色。
2. **Status & History**：狀態 + `status_history`（`StatusTransition` 列表）。
3. **Simulation**：每 item 的 `SimulationResult` —— `demand`/`revenue`/`gross_margin` 的 P10/P50/P90 帶（`ForecastBandChart` 或區間表），永不只顯 P50；彈性信心低時帶更寬，需標示。
4. **Optimization & Safe Action Set**：`PricingPlanComparison` —— 對每 item 顯示 `current_price` → `recommended_price`、`safe_action_set`（可行價集合）、`incremental_gross_margin`、`binding_constraints`（如 `max_increase_delta`、`margin_floor`、`max_price_ceiling`）。**Hard constraint 違規必須視覺顯眼**；`hard_constraint_violation_count` 必須為 0 才能送審，否則停用並列出 `constraint_violations`。
5. **Approval**：見 A8。
6. **Execution & Observation**：套用 `PriceTreatment`（from_price→to_price）後開啟 `ObservationWindow`（`stop_conditions` 預設：`max_gross_margin_drop_ratio` 0.05、`min/max_observation_days` 14/28、`min_sample_size` 30）。
7. **Effect & Rollback**：見 A9。
8. **Version / Audit**：`AuditMetadata` —— `policy_version`(`brand-pricing-policy-v1`)、`solver_version`、model/feature version、actor、reason、correlation_id。

## A8. Approval Rules (PriceOps)

`ApprovalPanel`（高風險，禁 optimistic）：

- 必填 `decision_reason`；提交鎖定防重送。
- 系統 `OptimizationResult`/建議與人工核准視覺分離；標示「由 solver 產生」與 `solver_version`。
- `requires_approval=true`（高 delta 或低信心）時不可略過核准。
- 成功顯示 `decision_id`、`actor_id`、`decision`、`approved_at`、`policy_version`、correlation_id。
- Segregation：建立者不得核准自身計畫，除非 policy 允許；唯讀使用者無動作鈕。
- 若有 hard constraint 違規或資料異常，核准鈕停用並說明原因。

## A9. Rollback & Effect Section

Rollback 是高風險可逆動作，UI 必須提供明確 affordance 與 audit：

- 顯示 `RollbackPlan`：`rollback_plan_id`、各 item `revert_to_price`、`trigger_conditions`。
- 評估（`PricingEffectEvaluation`）顯示 `baseline/expected/actual_incremental_gross_margin`、`impact_ratio`、`evidence_level`、`RollbackRecommendation`（`recommended`、`reason_code`：`negative_margin_impact`/`within_tolerance`、`impact_ratio`、`threshold`）。
- 當 `recommended_next_status=rollback` 或 `RollbackRecommendation.recommended=true`：以警示樣式提示，`執行 rollback` 為主要動作。
- 執行 rollback 禁 optimistic、必填 reason、寫 Audit；成功後 `status=rollback`（terminal）並顯示還原後價格。
- 觀察窗未成熟前不得宣稱調價成效；僅顯示 `observing` 與預期窗。

---

# Part B — AdLift 廣告增量工作台

## B3. Backend Vocabulary (authoritative)

AdLift 是 difference-in-differences（DiD）matched-pair 增量分析（`modules/adlift/domain/incrementality.py`）。Campaign 的核准/觀察/評估生命週期不在此模組，而是透過共用 `InterventionOps`（`InterventionKind.AD_CAMPAIGN`，由 `intervention_writeback` 寫回）。

| Concept | Backend source | Values / fields |
|---|---|---|
| Campaign | `AdCampaign` | `campaign_id`、`name`、`channel`(預設 `paid_search`)、`audience`、`creative`、`ad_spend`、`treatment_store_ids`、`candidate_control_store_ids`、`pre_period_start/end`、`campaign_period_start/end`、`campaign_intervention_id` |
| 配對控制 | `MatchedControl` | `treatment_store_id`、`control_store_id`、`match_distance`、`treatment_pre_avg`、`control_pre_avg`（greedy 1:1 nearest-pre-average、無放回；候選不足則 treatment 未配對） |
| 預趨勢 | `PreTrendResult` / `PreTrendStatus` | `status`(`PASS`/`FAIL`/`INCONCLUSIVE`/`NOT_TESTED`)、`treatment_slope`、`control_slope`、`slope_divergence`、`threshold`(預設 0.01) |
| 污染 | `ContaminationFinding` | `store_id`、`role`(treatment/control)、`intervention_ids`（campaign 窗內其他干預，上限 evidence 至 L2） |
| 增量估計 | `IncrementalityEstimate` / `EffectInterval` | `surface_revenue`、`incremental_revenue`、`incremental_gross_margin`、`effect_interval`(metric/low/point/high) |
| 報告 | `IncrementalityReport` | 見 §B6；含 `iromi`、`evidence_level`、`causal_claim_allowed`、`recommendation`、`measurement_method`(`DID`)、版本與 `report_card` |
| 證據等級 | `EvidenceLevel` | `L0`–`L5`（v1 只產 `L0`–`L3`）；causal 需 ≥ `L3` |
| 建議 | `Recommendation` | `CONTINUE`、`SCALE`、`STOP`、`CHANGE_CHANNEL`、`INCONCLUSIVE` |
| 版本 | constants | `model_version`(`adlift-did-v1`)、`feature_version`(`matched-control-view-v1`)、`policy_version`(`causal-evidence-level-v1`) |

iROMI 門檻：`SCALE_IROMI=1.5`、`CONTINUE_IROMI=1.0`（`iromi = incremental_gross_margin / ad_spend`，ad_spend≤0 時為 0.0）。

## B4. Evidence Ladder & Recommendation (UI gates)

證據等級由 `assign_evidence_level` 決定，UI 永不得高估確定性：

| Evidence | 條件 | causal_claim_allowed |
|---|---|---|
| `L0` | 無 treatment 期間資料 | 否 |
| `L1` | 有 treatment、無 control（before/after） | 否 |
| `L2` | 有 matched control，但 pre-trend 非 `PASS` 或有 contamination | 否 |
| `L3` | matched control + pre-trend `PASS` + 無 contamination（DiD validated） | 是 |

`recommend` 規則：`evidence < L3` → `INCONCLUSIVE`（不做 continue/stop 判斷）；`iromi ≥ 1.5` → `SCALE`；`iromi ≥ 1.0` → `CONTINUE`；否則 `STOP`。UI 必須據此呈現，且 `causal_claim_allowed=false` 時明確標「僅描述、不可宣稱因果」。

## B5. Routes & Page Jobs

| Route | Page | Primary job | Default density | Main components |
|---|---|---|---|---|
| `/w/pricing/adlift` | Campaign 列表 | 掃描 campaign、配對品質、pre-trend 與證據等級 | compact | `Table` + status badge + Drawer |
| `/w/pricing/adlift/:campaignId` | Campaign / Lift 報告詳情 | 配對控制、檢視 pre-trend、核准、增量報告 | comfortable | `AdLiftReportCard` + `EvidencePanel` + `ApprovalPanel` + `AuditMetadata` |

### B5.1 Campaign 列表 Table Columns

| Column | Required behavior |
|---|---|
| Campaign | `campaign_id` + `name` + `channel` |
| Period | pre-period 與 campaign-period 起訖 |
| Ad spend | `ad_spend` |
| Control match | treatment/control 店數；未配對 treatment 須標示 |
| Pre-trend | `PreTrendStatus`（PASS/FAIL/INCONCLUSIVE/NOT_TESTED）+ icon |
| Evidence | `EvidenceLevel`（L0–L3）+ `causal_claim_allowed` |
| iROMI / Rec | `iromi` + `recommendation` |
| Action | open、執行增量分析、送審（→ InterventionOps）、核准 |

## B6. Campaign / Lift 報告詳情 Page

固定區段順序（不得重排）：

1. **Summary**：campaign、channel、period、ad_spend、treatment 店數、目前 evidence level 與 recommendation。
2. **Matched Controls**：列出每組 `MatchedControl`（treatment↔control、`match_distance`、`treatment_pre_avg`、`control_pre_avg`）；未配對 treatment 店明確標示。配對距離越大代表越不可比，需以 icon/文字提示。
3. **Pre-trend Check**：`PreTrendResult` —— `status`、`treatment_slope`、`control_slope`、`slope_divergence`、`threshold`。**`FAIL` 或 `NOT_TESTED`/`INCONCLUSIVE` 時明確標「Evidence Level ≤ L2，不可宣稱因果」**，並阻擋升級因果宣稱。
4. **Contamination**：列出 `ContaminationFinding`（store、role、intervention_ids）；有污染即上限 L2，需顯眼提示。
5. **Incrementality Report**：`AdLiftReportCard` + 區間圖，見 §B7。
6. **Approval handoff**：`ApprovalPanel`（高風險、禁 optimistic、必填 reason、segregation）；campaign 的核准/觀察走共用 InterventionOps（`intervention_writeback`：`intervention_type=ad_campaign`、`evidence_level`、`recommendation`、`causal_claim_allowed`），詳見 `ODAY_PLUS_INTERVENTION_WORKFLOW_UI_SPEC.md`。
7. **Version / Audit**：`AuditMetadata` —— `model_version`(`adlift-did-v1`)、`feature_version`、`policy_version`、`report_version`、`generated_at`、`source_snapshot_ids`、`label_registry_entry`、correlation_id。

## B7. Incrementality Report Presentation

報告以 `AdLiftReportCard` contract（component contracts §5.9）呈現，欄位對應 `IncrementalityReport.to_report_card()`：`campaign`、`treatmentStores`、`controlStores`、`preTrendStatus`、`incrementalRevenue`、`incrementalGrossMargin`、`iromi`、`evidenceLevel`、`continueStopRecommendation`。

Report 必須：

- **分離 surface revenue / `incremental_revenue` / `incremental_gross_margin`**（AC-07-03，不可混為一談）；surface 為原始觀測，incremental 為 DiD 估計。
- 顯示 `measurement_method=DID`（無 control 時退為 before/after，evidence 僅 `L1`）。
- 顯示 `effect_interval`（per-store-day 效果的 low/point/high 離散度）作為不確定性，附資料表替代與僅可見資料 export。
- 顯示 `iromi` 與 `recommendation`，並標示判讀：`evidence < L3` → `INCONCLUSIVE`；`iROMI ≥ 1.5` → `SCALE`；`iROMI ≥ 1.0` → `CONTINUE`；否則 `STOP`。
- 以 `EvidencePanel` 置頂呈現 `pre_trend`、`contamination` 與 `evidence_level`；`causal_claim_allowed=false` 時 incremental 數值需標「描述性、非因果估計」，不得呈現為已驗證因果。
- `PreTrendStatus`、`EvidenceLevel`、`recommendation` 一律 文字 + icon/pattern + tooltip，不可只靠顏色。

---

## 3. Cross-Cutting Rules (Pricing & AdLift)

### 3.1 Empty / Loading / Error / Permission

| State | Required UI |
|---|---|
| Loading | section/chart skeleton；不顯示假價格、假 lift、假 evidence level |
| Empty | 尚無計畫/campaign：建立入口（具權限時） |
| Error | error summary + code + `correlation_id` + retry + timestamp；部分失敗區塊級 degraded |
| Permission | 唯讀或 403 依路由與 segregation policy |

### 3.2 High-Risk Action Rules

- 調價核准、rollback、campaign 核准、敏感欄位 export 皆禁 optimistic、必填 reason、寫 backend Audit。
- 成功才更新 UI 並顯示 `decision_id`/`plan_id`/`campaign_id` + correlation_id；失敗保留表單值。
- Hard constraint 違規（PriceOps）或 pre-trend FAIL（AdLift）必須阻擋對應升級動作並顯眼說明。

### 3.3 Accessibility & Responsive

- 所有狀態/風險/evidence 一律 文字 + icon/pattern + tooltip；圖表須有資料表替代。
- 表格支援 keyboard row focus、`aria-sort`、drawer focus trap。
- `lg+`：完整模擬/最佳化/報告 + sticky `ApprovalPanel`；`md`：單欄 + 底部 action bar；`sm`：摘要與輕量核准，完整審查提示桌機。
- Density：列表 `compact`，詳情 `comfortable`；density 不改語意顏色。

## 4. Handoff Checklist

- [ ] PriceOps 用 `PlanStatus` 12 值與服務方法流程，啟動前強制 `RollbackPlan`。
- [ ] 模擬顯示 demand/revenue/gross_margin P10/P50/P90，永不只顯 P50。
- [ ] `PricingPlanComparison` 顯示 safe action set、recommended price、binding constraints；hard 違規顯眼且 `hard_constraint_violation_count` 必為 0 才送審。
- [ ] 調價/廣告核准禁 optimistic、必填 reason、segregation，成功顯示 decision_id。
- [ ] Rollback 有明確 affordance、`RollbackRecommendation` 判讀、reason + audit，成功後狀態 `rollback`。
- [ ] AdLift 顯示 `MatchedControl`（含 `match_distance`、pre-avg）、`PreTrendStatus`（PASS/FAIL/INCONCLUSIVE/NOT_TESTED）與 contamination，非 PASS 或有污染鎖 ≤L2 不可宣稱因果。
- [ ] 增量報告以 `AdLiftReportCard` 分離 surface/incremental revenue 與 incremental gross margin，顯示 `effect_interval`、`iromi` 與 `recommendation`，`causal_claim_allowed=false` 時標描述性非因果。
- [ ] 四態、權限、responsive、a11y、URL state 全部可逐條驗收。
