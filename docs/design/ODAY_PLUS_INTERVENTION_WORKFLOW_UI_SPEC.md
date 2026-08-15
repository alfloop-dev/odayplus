---
doc_id: ODP-UXD-004-INTERVENTION-WORKFLOW-UI-SPEC
title: "ODay Plus Intervention Workflow UI Spec"
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
  - docs/design/ODAY_PLUS_OPERATIONS_ALERT_UI_SPEC.md
  - docs/design/ODAY_PLUS_PRICING_AND_ADLIFT_UI_SPEC.md
  - modules/intervention/domain/lifecycle.py
  - docs_archive/05_module_design/ODP-MOD-05_INTERVENTIONOPS.md
---

# ODay Plus Intervention Workflow UI Spec

## 1. Purpose & Boundary

本文件定義 `operations` workspace 內 InterventionOps 干預工作台的畫面任務、資訊層級、互動與狀態。InterventionOps 是 treatment-agnostic 的共用生命週期，被 PriceOps、AdLift、promotion、CRM recall、maintenance、cleaning 重用；本文件定義單一狀態機、衝突控制、觀察窗與證據模型在前端的呈現，讓 R4-004（InterventionOps 前端）可在無臨時視覺決策下實作。

範圍：

- **In scope**：`/w/operations/interventions`（干預佇列）與 `/w/operations/interventions/:interventionId`（生命週期詳情）的畫面、互動、狀態、衝突、核准、觀察窗、結果成熟度與證據等級。
- **Out of scope**：R0 AppShell chrome、ForecastOps 預警頁（見 `ODAY_PLUS_OPERATIONS_ALERT_UI_SPEC.md`）、PriceOps/AdLift 各自的 treatment 內頁（見 `ODAY_PLUS_PRICING_AND_ADLIFT_UI_SPEC.md`）、後端因果評估實作。
- **Source of truth**：元件 props/states 看 `ODAY_PLUS_COMPONENT_CONTRACTS.md`；後端字彙以 `modules/intervention/domain/lifecycle.py` 為準。

## 2. Backend Vocabulary (authoritative)

| Concept | Backend source | Values / fields |
|---|---|---|
| 生命週期狀態 | `InterventionStatus` | 見 §4（15 狀態） |
| Treatment 類型 | `InterventionKind` | `PRICE_CHANGE`、`AD_CAMPAIGN`、`PROMOTION`、`CRM_RECALL`、`MAINTENANCE`、`CLEANING`、`OPENING_CAMPAIGN`、`EXTERNAL_SHOCK` |
| 干預物件 | `Intervention` | `intervention_id`、`store_id`、`kind`、`status`、`trigger_ref`、`expected_outcome`、`planned_start/end`、`window_spec`、`created_by`、`history`、`policy_version` |
| 衝突 | `ConflictResult` | `has_conflict`、`conflicting_ids`、`conflicting_kinds`、`resolved`、`resolution_reason`、`blocks_approval` |
| 觀察窗 | `ObservationWindowSpec` / `ObservationWindow` | `outcome_window_days`、`maturity_buffer_days`、`opened_at`、`outcome_window_end`、`maturity_time`、`is_mature` |
| 結果 | `InterventionOutcome` | `incremental_revenue`、`incremental_gross_margin`、`has_control_group`、`pretrend_status`、`treatment/control_store_count`、`evaluation_method`、`randomized`、`ad_spend`、`iromi` |
| 證據等級 | `EvidenceLevel` | `L0`–`L5`（anecdotal → policy-ready） |
| 預趨勢 | `PretrendStatus` | `PASS` / `FAIL` / `INCONCLUSIVE` |
| 評估方法 | `EvaluationMethod` | `NONE`、`BEFORE_AFTER`、`MATCHED_BEFORE_AFTER`、`DID`、`SYNTHETIC_CONTROL`、`RCT` |
| 效果評估 | `EffectEvaluation` | `evidence_level`、`can_claim_effect`、`can_claim_causal`、`recommendation`、`observation_mature`、`limitations` |
| 建議 | `Recommendation` | `CONTINUE`、`SCALE`、`STOP`、`CHANGE_CHANNEL`、`INCONCLUSIVE` |
| 核准 | `ApprovalRecord` | `approved`、`actor_id`、`decision_reason`(required)、`approved_at`、`policy_version` |
| 轉移審計 | `InterventionTransition` | `from_status`、`to_status`、`actor`、`action`、`reason`、`at`、`correlation_id` |

## 3. Decision Separation

| Layer | Intervention 意義 | UI 呈現 |
|---|---|---|
| Prediction | 觸發來源（ForecastOps 預警 `trigger_ref`、預測 gap） | trigger 摘要 + 連結回預警 |
| Recommendation | eligibility 結果、建議 action set | eligibility block、proposed action |
| Human decision | 核准/退回（`ApprovalRecord` + reason + 衝突解決） | `ApprovalPanel` |
| Execution | 執行 treatment、開啟觀察窗 | `InterventionTimeline` execution + observation 節點 |
| Outcome | 結果成熟後的 incremental 與 evidence level | Outcome / Effect 區段；未成熟不得宣稱因果 |

## 4. Lifecycle State Machine (UI)

固定狀態機（`InterventionStatus`，ODP-MOD-05 §7）：

```text
CANDIDATE
  → ELIGIBILITY_CHECKING → ELIGIBLE | INELIGIBLE
  → ACTION_PROPOSED
  → CONFLICT_CHECKING            (overlap / contamination control)
  → PENDING_APPROVAL → APPROVED | REJECTED   (human approval, separated from execution)
  → EXECUTING → OBSERVING        (observation window opens at execution)
  → EVALUATING → COMPLETED | STOPPED | ROLLED_BACK
```

UI 規則：

- **Terminal**（不可再轉移，灰化動作）：`INELIGIBLE`、`REJECTED`、`COMPLETED`、`STOPPED`、`ROLLED_BACK`。
- **Active / 競用時間軸**（會觸發衝突檢查，需在 timeline 強調）：`ACTION_PROPOSED`、`CONFLICT_CHECKING`、`PENDING_APPROVAL`、`APPROVED`、`EXECUTING`、`OBSERVING`、`EVALUATING`。
- 每個狀態徽章必須 文字 + icon/pattern，不可只靠顏色。
- 狀態只能依後端允許的轉移前進；UI 不得樂觀跳狀態，操作後以回傳的 `Intervention.status` 為準。

`InterventionTimeline` 固定節點順序：Triggered → Eligibility → Action built → Conflict checked → Approved → Executed → Observation Window → Outcome → Effect evaluated → Closed。每節點顯示對應狀態、actor、`at`、reason。

## 5. Routes & Page Jobs

| Route | Page | Primary job | Default density | Main components |
|---|---|---|---|---|
| `/w/operations/interventions` | 干預佇列 | 掃描各店干預、狀態、衝突與觀察窗到期 | compact | `Table` + status badge + Drawer |
| `/w/operations/interventions/:interventionId` | 干預生命週期詳情 | 走 eligibility → 衝突 → 核准 → 執行 → 觀察 → 評估 | comfortable | `InterventionTimeline` + `ApprovalPanel` + `EvidencePanel` + `AuditMetadata` |

## 6. Shared Page Contract

- Page Header：breadcrumb（`營運 Operations > Interventions > Entity`）、title、summary、status badge、primary action（依狀態與權限）、secondary actions、last updated。
- Filter Bar：`store_id`、`kind`、`status`、`has_conflict`、觀察窗到期、date range、selected entity 皆進 URL query。
- Content：loading、empty、error、permission 四態。
- Right Drawer：列表選中項快速查看，支援 deep link、上一筆/下一筆、Esc、focus trap。
- Data freshness / policy：顯示 `policy_version`（`intervention-lifecycle-policy-v1`）、`feature_version`。
- Permission：無權限不顯示入口與操作；segregation 規則見 §10。

## 7. 干預佇列 Page

### 7.1 Page Header

- Title：`干預工作台`
- Summary：`分流各店干預案、處理衝突、核准、觀察與效果評估。`
- Status：各狀態計數（pending approval、observing、待評估、有衝突）。
- Primary action：依列 `檢查資格`、`送審`、`核准`、`評估效果`。

### 7.2 Table Columns

| Column | Required behavior |
|---|---|
| Intervention | `intervention_id` + `store_id` |
| Kind | `InterventionKind`（8 值）+ icon |
| Status | `InterventionStatus`（15 值）+ icon/pattern；terminal 灰化 |
| Trigger | `trigger_ref`（多為 ForecastOps `alert_id`）連結回預警 |
| Conflict | `has_conflict` 時顯示衝突筆數；`blocks_approval` 以警示樣式標示 |
| Observation | 觀察窗狀態：開啟時間、`outcome_window_end`、`maturity_time`、是否 `is_mature` |
| Evidence | 已評估時顯示 `EvidenceLevel`（L0–L5）+ `recommendation` |
| Owner / SLA | `created_by` + 待處理時間 |
| Action | open detail、檢查資格、衝突檢查、送審、核准、執行、評估 |

### 7.3 Row Drawer

Drawer 顯示干預摘要、目前 timeline 節點、衝突摘要、觀察窗倒數與主要 next action：`開啟生命週期詳情`。完整核准只在 detail page 的 `ApprovalPanel` 執行。

## 8. 干預生命週期詳情 Page

固定區段順序（不得重排）：

1. **Summary**：`store_id`、`kind`、目前 `status`、`expected_outcome`、`trigger_ref`、需處理摘要。
2. **Status & Timeline**：`InterventionTimeline`（§4 節點），含每節點 actor / `at` / reason。
3. **Eligibility**：`EligibilityResult` —— eligible/ineligible 與 reasons；ineligible 為 terminal，顯示原因並關閉後續動作。
4. **Action Proposed**：`action_spec` 摘要（如 `price_change_pct`、`campaign`、`rollback_plan`）。
5. **Conflict Control**：見 §9。
6. **Approval**：`ApprovalPanel`，見 §10。
7. **Execution & Observation Window**：見 §11。
8. **Outcome & Effect**：見 §12。
9. **Version / Audit**：`AuditMetadata` —— `policy_version`、`feature_version`、完整 `history`（`InterventionTransition` 列表：from/to status、actor、action、reason、at、correlation_id）。

## 9. Conflict Control Section

衝突控制防止同店、時間窗重疊的干預互相污染（`detect_conflicts` 比對 `planned_start`..`effective_window_end`，僅含 `ACTIVE_CONFLICT_STATUSES`，並把觀察窗 `maturity_time` 納入有效結束時間）。

UI 必須：

- 顯示 `ConflictResult`：`has_conflict`、`conflicting_ids`（連結各干預）、`conflicting_kinds`、`checked_at`。
- 當 `blocks_approval`（`has_conflict and not resolved`）為真：核准按鈕停用，並以警示樣式說明「需先解決或覆寫衝突」。
- 覆寫衝突（allow overlap）為高風險：必填 `resolution_reason`，禁 optimistic，寫 Audit；成功後 `resolved=true` 並記錄 reason。
- 衝突不可只用紅色；需文字 + icon + 受影響干預清單。

## 10. Approval & Segregation Rules

`ApprovalPanel`（高風險，禁 optimistic）：

- 核准與退回皆**必填 `decision_reason`**（後端驗證非空）；提交鎖定防重送。
- 必須先通過 `PENDING_APPROVAL`；若衝突 `blocks_approval` 則停用核准。
- 系統 eligibility/建議與人工決策視覺分離，標示「由系統建議」與 `policy_version`。
- 成功回傳顯示 `approved`(true/false)、`actor_id`、`approved_at`、`policy_version`、`correlation_id`。
- Segregation：建立者不得核准自己的案，除非 policy 明確允許；唯讀使用者只見狀態與 audit、無動作鈕。
- 失敗保留表單值並顯示 field/global error + `correlation_id`。

決策動作對應：

| Action | 後端對應 | 必填 |
|---|---|---|
| 檢查資格 | eligibility | actor、reasons |
| 提出 action | action | `action_spec`、actor |
| 衝突檢查 / 覆寫 | conflict-check | actor、`allow_overlap`、`reason`（覆寫時必填） |
| 送審 | submit | actor |
| 核准 / 退回 | approve（`APPROVE`/`REJECT`） | actor、`decision_reason` |
| 執行 | execute | executor、`executed_at` |
| 收集結果 | outcomes | actor、incremental_*、control/pretrend 等 |
| 評估效果 | evaluate | actor、`replicated`、`now` |

## 11. Execution & Observation Window Section

- 執行（`EXECUTING → OBSERVING`）後才開啟觀察窗；UI 不得在執行前宣稱觀察中。
- 顯示 `ObservationWindow`：`opened_at`、`outcome_window_days`、`maturity_buffer_days`、`outcome_window_end`、`maturity_time`，並以倒數呈現是否 `is_mature`。
- 各 kind 預設窗（`window_spec`）需顯示來源，例：`PRICE_CHANGE` 21+7、`AD_CAMPAIGN` 14+7、`PROMOTION` 7+7、`MAINTENANCE` 10+3、`OPENING_CAMPAIGN`/`EXTERNAL_SHOCK` 28+7（outcome + maturity buffer 天）。
- 觀察窗未到 `maturity_time` 前，Outcome 區只能顯示 `OBSERVING` 與預期窗，不得宣稱成效。

## 12. Outcome Maturity & Evidence Section

結果成熟度與證據等級是高風險頁面的核心，UI 永不得高估確定性：

- **Effect 宣稱閘門**：`can_claim_effect` 需 `EvidenceLevel ≥ L1`（觀察窗成熟）；未成熟一律 `L0`，僅可顯示 `OBSERVING`。
- **Causal 宣稱閘門**：`can_claim_causal` 需 `EvidenceLevel ≥ L3`（matched control + 通過 pre-trend）。
- 證據等級階梯（`resolve_evidence_level`）：
  - `L0` 未成熟（anecdotal）
  - `L1` 成熟、無 control（before/after）
  - `L2` 有 control、pre-trend 非 PASS（matched descriptive）
  - `L3` control + pre-trend PASS（DiD）
  - `L4` randomized（RCT）
  - `L5` 已複製（policy-ready）
- 顯示 `InterventionOutcome`：`incremental_revenue`、`incremental_gross_margin`、`iromi`（= incremental_gross_margin / ad_spend，ad_spend≤0 時為 None）、`treatment/control_store_count`、`evaluation_method`、`pretrend_status`。
- 顯示 `EffectEvaluation`：`evidence_level`、`recommendation`（CONTINUE/SCALE/STOP/CHANGE_CHANNEL/INCONCLUSIVE）、`observation_mature`、`limitations`（如 `observation_window_not_mature`、`no_control_group`、`pretrend_fail`）。
- `EvidencePanel` 必須把 `limitations` 與低證據等級置頂呈現，不可藏在 tooltip；無 control 或 pre-trend FAIL 時明確標示「僅描述、不可宣稱因果」。
- `pretrend_status` 與 `evidence_level` 皆 文字 + icon；`incremental_*` 在 `can_claim_effect=false` 時不得顯示數值（後端回 0 / None）。

## 13. Empty / Loading / Error / Permission

| State | Required UI |
|---|---|
| Loading | timeline/section skeleton；不顯示假狀態或假 evidence level |
| Empty | 尚無干預：自預警 `建立干預` 入口（具權限時） |
| Error | error summary + code + `correlation_id` + retry + timestamp；部分失敗區塊級 degraded |
| Permission | 唯讀或 403 依路由與 segregation policy |

## 14. Accessibility & Responsive

- 所有狀態/衝突/evidence 一律 文字 + icon/pattern + tooltip。
- 表格支援 keyboard row focus、`aria-sort`、drawer focus trap。
- `lg+`：完整 timeline + 側欄摘要 + sticky `ApprovalPanel`。
- `md`：單欄區段、底部 sticky action bar（核准可用）。
- `sm`：摘要、目前節點、輕量核准與任務回覆；完整生命週期審查提示使用桌機。
- Density：列表 `compact`，詳情 `comfortable`。

## 15. Handoff Checklist

- [ ] 生命週期用 `InterventionStatus` 15 值、`InterventionKind` 8 值，terminal 與 active 區分明確。
- [ ] `InterventionTimeline` 節點順序固定，每節點顯示 actor/at/reason。
- [ ] 衝突區顯示 `conflicting_ids`/`kinds`，`blocks_approval` 時停用核准並要求 `resolution_reason`。
- [ ] `ApprovalPanel` 禁 optimistic、必填 `decision_reason`、執行 segregation，成功顯示 actor/approved_at/policy_version。
- [ ] 觀察窗顯示 opened_at/outcome_window_end/maturity_time 與 `is_mature`，未成熟不宣稱成效。
- [ ] Outcome 區依 evidence level 閘門：`can_claim_effect`(≥L1)、`can_claim_causal`(≥L3)，limitations 置頂。
- [ ] Version/Audit 區呈現完整 `history`、policy/feature version 與 correlation id。
- [ ] 四態、權限、responsive、a11y、URL state 全部可逐條驗收。
