---
doc_id: ODP-UXD-005-LEARNING-HUB-UI-SPEC
title: "ODay Plus Learning Hub UI Spec"
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
  - docs/design/ODAY_PLUS_ASSET_AND_NETPLAN_UI_SPEC.md
  - docs/design/ODAY_PLUS_AUDIT_EVIDENCE_UI_SPEC.md
  - modules/learninghub/application/release.py
  - models/shared_ml/registry.py
  - models/shared_ml/model_card.py
  - models/shared_ml/validation.py
  - docs_archive/05_module_design/ODP-MOD-10_LEARNING_HUB.md
---

# ODay Plus Learning Hub UI Spec

## 1. Purpose & Boundary

本文件定義 `ai`（AI／資料）workspace 內 **Learning Hub** 的前端規格：模型登錄（registry）、驗證（validation）、模型卡（model card）、發布控制（shadow/canary/full）與 rollback console。這是模型治理的高風險面——錯誤的發布或漏掉的回滾條件會直接污染線上決策——所以 UI 必須讓 AI/資料團隊在發布前看清驗證結果、模型卡完整度與核准狀態，並把 release/rollback 當作禁 optimistic、必觸發 Audit 的閘門。本文件讓 R6-003 前端可在無臨時視覺決策下實作。

範圍：

- **In scope**：`/w/ai/models`、`/w/ai/models/:modelName`、`/w/ai/models/:modelName/:version`、`/w/ai/releases`、`/w/ai/releases/:releaseId` 的畫面、互動、stage 生命週期、validation gate、model card 完整度、release controller 與 rollback console。
- **Out of scope**：R0 AppShell chrome、MLflow adapter 與訓練/驗證演算法實作、各業務模組（PriceOps/AVM/NetPlan）自己的決策頁、決策稽核與證據匯出格式（見 `ODAY_PLUS_AUDIT_EVIDENCE_UI_SPEC.md`）。Data Quality / drift 監控頁面為 `ai` workspace 同層的鄰頁，本文件只在 release gate 引用其狀態，不定義其內頁。
- **Source of truth**：元件 props/states 看 `ODAY_PLUS_COMPONENT_CONTRACTS.md`；stage/alias 字彙以 `models/shared_ml/registry.py`、模型卡以 `models/shared_ml/model_card.py`、驗證以 `models/shared_ml/validation.py`、發布以 `modules/learninghub/application/release.py` 為準。導覽 key（`ai`）與 route 結構以 `ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md` 為準。

## 2. Decision Separation

Learning Hub 必須分離下列語意，不得混在同一個「模型已就緒」結論：

| Layer | Learning Hub 意義 | UI 呈現 |
|---|---|---|
| Prediction / Evidence | validation 指標、segment 指標、calibration、baseline 對比 | validation 區、`ModelReleaseCard.metricSummary`/`segmentRegression` |
| Recommendation | 通過驗證、模型卡完整且已簽核 → 可申請發布 | release 前置條件 checklist（系統判定） |
| Human decision | 發布 / 回滾的核准（actor + reason + 核准 id） | `ApprovalPanel` |
| Execution | stage 轉移（shadow/canary/full/rollback）、alias 重指 | release controller / rollback console |
| Outcome | 監控窗、success/fail criteria、drift | 監控區（引用 Data Quality） |

## 3. Backend Vocabulary (authoritative)

| Concept | Backend source | Values / fields |
|---|---|---|
| 模型階段 | `ModelStage` | `dev`、`shadow`、`canary`、`production`、`retired`、`rolled_back`、`blocked` |
| 模型別名 | `ModelAlias` | `champion`、`challenger`、`shadow`、`canary`、`production`、`previous_production`、`archived` |
| 模型版本 | `ModelVersion` | `model_name`、`version`、`artifact_uri`、`dataset_snapshot_id`、`feature_schema_version`、`label_version`、`metrics`、`stage`、`aliases`、`run_id`、`git_sha`、`created_at`、`approved_by`、`approved_at`、`rollback_target`、`monitoring_config` |
| 模型卡 | `ModelCard` | `owner`、`risk_level`、`intended_use`、`not_intended_use`、`dataset_snapshot_id`、`validation_run_id`、`feature_set_id`、`label_set_id`、`training_period`、`validation_period`、`algorithm`、`baseline`、`metrics_summary`、`segment_metrics`、`calibration_summary`、`explainability_method`、`limitations`、`known_biases`、`privacy_review`、`security_review`、`release_status`、`rollback_conditions`、`approvals`、`is_complete`、`is_approved` |
| 模型卡簽核 | `ModelCardApproval` | `approver`、`role`、`decision`(預設 approved)、`approved_at` |
| 風險等級 | `ModelRiskLevel` | `R1`、`R2`、`R3`、`R4`（R4 最高） |
| 驗證執行 | `ValidationRun` | `validation_run_id`、`dataset_snapshot_id`、`metrics`、`baseline_metrics`、`passed`、`thresholds`、`segment_metrics`、`calibration_summary` |
| 指標門檻 | `MetricThreshold` | `metric_name`、`threshold_type`(`>=`/`<=`)、`threshold_value` |
| 分群指標 | `SegmentMetric` | `segment_name`、`metrics` |
| 審查狀態 | `validation.py` 常數 | `PASSED`、`WARNING`、`FAILED`（privacy/security review） |
| 發布類型 | `ReleaseType` | `SHADOW`、`CANARY`、`FULL`、`ROLLBACK` |
| 發布決策 | `ModelReleaseDecision` | `release_id`、`model_name`、`from_version`、`to_version`、`release_type`、`reason`、`approval_id`、`rollback_target`、`monitoring_window`、`success_criteria`、`fail_criteria`、`affected_modules`、`requested_by`、`approved_by`、`created_at`、`audit_event_id` |
| 資料快照 | `DatasetSnapshot` | `dataset_snapshot_id`、`num_records`、`training_eligible`、`created_at` |

## 4. Release Gate (前置條件，UI 永不得略過)

`request_release`（`modules/learninghub/application/release.py`）在發布前強制檢查；UI 必須以 checklist 呈現，未全綠不得啟用「申請發布」：

1. **Validation passed**：對應 `ValidationRun.passed`（每條 `MetricThreshold` 依 `>=`/`<=` 與 `baseline_metrics` 比對）。
2. **Model card complete**：`ModelCard.is_complete` —— 必填文字欄位、`metrics_summary`、`rollback_conditions` 皆非空，且 `privacy_review`/`security_review` 非 `FAILED`（`WARNING` 須顯眼提示但不阻擋）。
3. **Model card approved**：`ModelCard.is_approved` —— 至少一筆 `decision="approved"` 的 `ModelCardApproval`。
4. **Rollback target present**：`FULL`/`CANARY`/`ROLLBACK` 必須有 `rollback_target`，否則停用發布並說明。

任一未滿足：對應 checklist 項以 red/orange token + 文字標示，並指向需補的區段。

## 5. Stage Lifecycle & Release Semantics (UI)

```text
dev → shadow → canary → production
               (rollback) → rolled_back / 回復 previous_production
blocked / retired 為旁支終態
```

各 `ReleaseType` 的語意（UI 須據此預示 alias 變動，但以回傳結果為準）：

- **SHADOW**：→ `ModelStage.shadow`，設 `shadow` alias。
- **CANARY**：→ `ModelStage.canary`，設 `canary` alias。
- **FULL**：舊 production 退為 `retired`，新版升 `production` 並設 `production` + `champion` alias，舊版標 `previous_production`。
- **ROLLBACK**：依 `rollback_target` 將現版轉 `rolled_back`，把 target 回復為 `production` + `champion`，清除 `previous_production`。

UI 規則：

- stage 與 alias 一律 文字 + icon/pattern；顏色用 design tokens `color.model.*`（`production` purple.700、`candidate/challenger` purple.500、`shadow` blue.500、`canary` blue.700、`rollback` red.500，見 tokens §`color.model`）。
- 不得樂觀切換 stage；以回傳 `ModelVersion.stage`/`aliases` 為準。`ModelVersionBadge`（§4.14）顯示版本 + stage。

## 6. Routes & Page Jobs

| Route | Page | Primary job | Default density | Main components |
|---|---|---|---|---|
| `/w/ai/models` | 模型登錄列表 | 掃描各模型最新版本、stage、validation 與 release 狀態 | compact | `Table` + `ModelVersionBadge` + Drawer |
| `/w/ai/models/:modelName` | 模型版本歷史 | 比較同模型各版本（champion/challenger/shadow/canary） | compact | 版本比較 `Table` + `ModelReleaseCard` |
| `/w/ai/models/:modelName/:version` | 模型版本詳情 | 看 model card、validation、申請發布/回滾 | comfortable | `ModelReleaseCard` + model card 區 + `ApprovalPanel` + `AuditMetadata` |
| `/w/ai/releases` | 發布列表 | 掃描發布/回滾事件、監控窗、success/fail criteria | compact | `Table` + status badge + Drawer |
| `/w/ai/releases/:releaseId` | 發布詳情 | 看單次發布的條件、核准、影響模組與 Audit | comfortable | release detail + `DecisionAuditTimeline` + `AuditMetadata` |

## 7. 模型登錄列表 Page

- Title：`模型登錄`；Summary：`管理各模型版本的驗證、模型卡與上線階段。`
- Primary action：依權限 `登錄新版本` / `申請發布`。
- Filter Bar（皆進 URL query）：`model_name`、`stage`、`risk_level`、validation passed、是否待核准、date range、selected entity。
- Table columns：

| Column | Required behavior |
|---|---|
| Model | `model_name` |
| Version | `version` + `ModelVersionBadge`（stage 色） |
| Stage / Alias | `stage` + 主要 alias（champion/challenger/shadow/canary）+ icon/pattern |
| Risk | `risk_level`（R1–R4）；R3/R4 須警示 |
| Validation | passed / failed（+ 關鍵指標 vs threshold） |
| Card | model card 完整度（is_complete）+ 簽核（is_approved） |
| Data quality / Drift | 引用鄰頁狀態（PASSED/WARNING/FAILED） |
| Action | open、登錄、申請發布、回滾 |

## 8. 模型版本詳情 Page

固定區段順序（不得重排）：

1. **Summary**：`model_name`:`version`、`stage` + alias、`risk_level`、validation passed、card complete/approved、`rollback_target`。
2. **Model Card**：呈現 `ModelCard` 全欄位，分群顯示：擁有者/風險/用途（`intended_use`/`not_intended_use`）、資料與特徵（`dataset_snapshot_id`/`feature_set_id`/`label_set_id`/training/validation period）、演算法/baseline、`metrics_summary`、`segment_metrics`、`calibration_summary`、`explainability_method`、`limitations`、`known_biases`、`privacy_review`/`security_review`、`rollback_conditions`、`approvals`。**`is_complete`/`is_approved` 以 checklist 呈現**；缺項指向對應欄位。
3. **Validation**：`ValidationRun` —— 對每條 `MetricThreshold` 以 explicit comparison 呈現 `metric`、`threshold_type`、`threshold_value`、實際值、是否達標（pass/fail icon），並與 `baseline_metrics` 並排比較（Δ）。`segment_metrics` 以表格列出，凸顯任何分群回歸（segment regression）。`passed=false` 時阻擋發布並標示未達標項。
4. **Release Controller**：見 §9。
5. **Rollback Console**：見 §10。
6. **Version / Audit**：`AuditMetadata` —— `model_version`、`feature_schema_version`、`label_version`、`run_id`、`git_sha`、`approved_by`、`approved_at`、`policy`/`monitoring_config`、correlation_id。

## 9. Release Controller

`ModelReleaseCard`（component contracts §5.12）+ `ApprovalPanel`：

- `ModelReleaseCard` 欄位：`modelId`、`version`、`championOrChallenger`、`metricSummary`、`segmentRegression`、`dataQualityStatus`、`driftStatus`、`releaseStage`、`rollbackTarget`、`approvalStatus`。
- **發布前置 checklist**（§4）必須全綠才啟用「申請發布」；對 `SHADOW`/`CANARY`/`FULL` 顯示該類型的 stage/alias 預期變動。
- 申請發布需填：`release_type`、`reason`、`approval_id`、`monitoring_window`、`success_criteria`、`fail_criteria`、`affected_modules`，`FULL`/`CANARY` 另需 `rollback_target`。
- **核准**（`ApprovalPanel`，禁 optimistic）：必填 reason；系統前置判定與人工核准視覺分離。成功回傳 `ModelReleaseDecision`（`release_id`、`from_version`→`to_version`、`release_type`、`approved_by`、`audit_event_id`）；release/rollback 觸發後端 Audit（`learninghub.model_release.v1`）。
- `affected_modules` 須顯眼列出（此發布會影響哪些業務模組），讓核准者理解 blast radius。
- Segregation：模型擁有者不得單獨核准自身高風險（R3/R4）發布，依 policy 需 model-review-board。

## 10. Rollback Console

Rollback 是高風險可逆動作，UI 必須提供明確 affordance 與證據：

- 僅當存在可回滾的 `rollback_target`（通常為 `previous_production`/`champion`）時可用；缺 target 時停用並說明。
- 顯示「現版 → 回滾目標」的 explicit comparison：版本、stage、關鍵指標差異、`rollback_conditions`（觸發回滾的條件，來自 model card）。
- 執行回滾禁 optimistic、必填 reason、寫 Audit；成功後現版 `stage=rolled_back`、target 回復 `production`+`champion`，UI 以回傳 `aliases` 更新，並於 `DecisionAuditTimeline` 留痕。
- 監控窗（`monitoring_window`）內若觸發 `fail_criteria`，以警示樣式建議回滾；但仍需人工確認，不自動回滾。

## 11. 發布列表 / 發布詳情 Page

- 發布列表 columns：`release_id`、`model_name` `from_version`→`to_version`、`release_type`、`approval_id`、`monitoring_window`、approved_by、created_at、Action(open)。`compact` 密度。
- 發布詳情：呈現 `ModelReleaseDecision` 全欄位、`success_criteria`/`fail_criteria`、`affected_modules`，並以 `DecisionAuditTimeline`（§5.13）顯示 Prediction→…→Feedback 固定節點與 `audit_event_id`。可匯出證據（見 `ODAY_PLUS_AUDIT_EVIDENCE_UI_SPEC.md`），匯出記 Audit。

## 12. Cross-Cutting Rules

### 12.1 Empty / Loading / Error / Permission

| State | Required UI |
|---|---|
| Loading | section/table skeleton；不顯示假指標、假 stage、假 passed |
| Empty | 尚無模型/版本/發布：登錄入口（具權限時） |
| Error | error summary + code + `correlation_id` + retry + timestamp；部分失敗區塊級 degraded |
| Permission | 唯讀或 403；高風險發布/回滾依 segregation policy |

### 12.2 High-Risk Action Rules

- 發布、回滾、模型卡簽核皆禁 optimistic、必填 reason、寫後端 Audit。
- 成功才更新 UI 並顯示 `release_id`/`decision_id`/`audit_event_id` + correlation_id；失敗保留表單值。
- 發布前置 checklist（§4）任一未滿足即阻擋「申請發布」並指向需補項。

### 12.3 Dense Executive Views & Comparison

- 列表/發布頁預設 `compact`；詳情 `comfortable`；review board 可切 `presentation`（不改語意色）。
- validation vs baseline、現版 vs 回滾目標、version 歷史一律 explicit comparison（並排 + Δ + pass/fail icon）。
- 所有指標表須有資料表替代與僅可見資料 export。

### 12.4 Accessibility & Responsive

- stage/alias/validation/risk 一律 文字 + icon/pattern + tooltip；`color.model.*` 不作為唯一訊號。
- 表格支援 keyboard row focus、`aria-sort`、drawer focus trap。
- `lg+`：完整 model card + validation 比較 + sticky `ApprovalPanel`；`md`：單欄 + 底部 action bar；`sm`：摘要與輕量核准，完整審查提示桌機。

## 13. Handoff Checklist

- [ ] stage 用 `ModelStage` 7 值、alias 用 `ModelAlias` 7 值，`color.model.*` token 對應且非唯一訊號。
- [ ] 發布前置 checklist 對應 `request_release` 四條件（validation passed、card complete、card approved、rollback_target for FULL/CANARY/ROLLBACK），未全綠不得申請發布。
- [ ] Model card 完整呈現全欄位並以 `is_complete`/`is_approved` checklist 標示缺項；privacy/security `FAILED` 阻擋、`WARNING` 提示。
- [ ] Validation 以 explicit comparison 呈現 metric/threshold/實際/baseline 與 segment regression，`passed=false` 阻擋發布。
- [ ] Release controller 顯示 release_type 的 stage/alias 變動與 `affected_modules`；核准禁 optimistic、必填 reason、寫 Audit、回傳 release_id + audit_event_id。
- [ ] Rollback console 顯示現版 vs target 比較與 `rollback_conditions`，執行禁 optimistic、必填 reason，成功後 stage/alias 依回傳更新。
- [ ] 發布詳情以 `DecisionAuditTimeline` 留痕並可匯出證據（匯出記 Audit）。
- [ ] 四態、權限、responsive、a11y、URL state、`presentation` 密度全部可逐條驗收。
