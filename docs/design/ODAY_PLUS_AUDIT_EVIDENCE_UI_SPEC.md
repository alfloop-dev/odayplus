---
doc_id: ODP-UXD-005-AUDIT-EVIDENCE-UI-SPEC
title: "ODay Plus Audit and Evidence UI Spec"
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
  - docs/design/ODAY_PLUS_LEARNING_HUB_UI_SPEC.md
  - shared/audit/events.py
  - shared/audit/policy.py
  - shared/auth/rbac.py
  - shared/auth/identity.py
  - apps/web/src/app/audit/page.tsx
---

# ODay Plus Audit and Evidence UI Spec

## 1. Purpose & Boundary

本文件定義 `audit`（稽核）workspace 與 `/admin/audit` 管理段內的 **決策稽核（Decision Audit）、Evidence 匯出、Decision Card 與補貼證據矩陣（Subsidy Evidence Matrix）** 前端規格。稽核面是平台的「可信任憑證」：每個高風險決策都必須能被追溯到「哪個模型在什麼時間用什麼特徵做出建議、誰在什麼時間用什麼理由核准、執行與結果如何、是否曾被覆寫或回滾」。UI 的天職是把這條鏈完整、不可被優化掩蓋地呈現，並讓匯出本身也留痕。本文件統一各業務模組（AVM/NetPlan/PriceOps/AdLift/Intervention/Learning Hub）共用的稽核呈現，讓 R6-003 前端可在無臨時視覺決策下實作。

範圍：

- **In scope**：`/w/audit/decisions`、`/w/audit/decisions/:decisionId`、`/w/audit/evidence`、`/admin/audit` 的畫面、互動、決策稽核時間軸、Decision Card、Evidence 匯出與補貼證據矩陣。
- **Out of scope**：R0 AppShell chrome、後端 Audit log 儲存與保留策略、各業務模組自己的決策內頁（核准在各 workspace 執行，本文件只定義其稽核呈現）、RBAC/ABAC policy 實作（見 `shared/auth/`）。
- **Source of truth**：稽核事件以 `shared/audit/events.py`、稽核策略與遮罩以 `shared/audit/policy.py`、Action/分級以 `shared/auth/rbac.py`、`shared/auth/identity.py` 為準；元件 props/states 看 `ODAY_PLUS_COMPONENT_CONTRACTS.md`。導覽 key（`audit`）、`/admin/audit` 與 route 結構以 `ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md §3.2/§7` 為準。

## 2. Decision Separation（稽核版）

稽核呈現必須讓四層在時間軸上各自可辨，不得把「模型建議」與「人工核准」混為「系統決定」：

| Layer | 稽核意義 | UI 呈現 |
|---|---|---|
| Prediction | 模型在 `feature_snapshot_time` 用 `model_version` 產生的預測 | timeline `Prediction generated` 節點 |
| Recommendation | 由預測推得的建議（policy_version） | timeline `Recommendation generated` 節點 |
| Human decision | 核准/退回/覆寫（actor、reason、time） | timeline `Human decision submitted` + Decision Card |
| Execution | 執行/發布/回滾 | timeline `Execution started` 節點 |
| Outcome | 觀察結果與回授 label registry | timeline `Outcome observed` / `Feedback written` 節點 |

## 3. Backend Vocabulary (authoritative)

| Concept | Backend source | Values / fields |
|---|---|---|
| 稽核事件 | `AuditEvent` (`shared/audit/events.py`) | `event_type`、`actor`、`action`、`resource`、`outcome`、`correlation_id`、`metadata`、`job_id`、`event_id`、`occurred_at` |
| 結果碼 | `AuditOutcome` (`shared/audit/policy.py`) | `allow`、`deny`、`success`、`failure` |
| 高風險動作 | `HIGH_RISK_ACTIONS` | `approve`、`execute`、`publish`、`override`、`rollback` |
| 必稽核動作 | `ALWAYS_AUDITED_ACTIONS` | 高風險 + `create`、`update`、`delete`、`export` |
| 動作 | `Action` (`shared/auth/rbac.py`) | `approve`、`execute`、`export`、`publish`、`override`、`rollback`、… |
| 資料分級 | `DataClassification` (`shared/auth/identity.py`) | `CONFIDENTIAL`(2)、`RESTRICTED`(3)、`HIGHLY_RESTRICTED`(4) |
| 稽核可見閾值 | `AUDIT_VISIBILITY_THRESHOLD` | `RESTRICTED`（達此級的檢視/匯出須稽核） |
| 安全事件型別 | `SECURITY_EVENT_TYPE` | `security.authorization`（所有授權決策） |
| 遮罩工具 | `policy.py` | `mask_phone`(末3碼)、`mask_email`(首字+網域)、`mask_text` |

業務模組產生的稽核事件型別（呈現於決策列表/時間軸）：

| 來源 | event_type | action | 典型 metadata |
|---|---|---|---|
| AVM | `avm.case_created.v1` / `avm.normalized.v1` / `avm.valued.v1` / `avm.finance_approved.v1` / `avm.dataroom_ready.v1` / `avm.dataroom_exported.v1` | create / value / approve / export | reason、idempotency_key |
| NetPlan | `netplan.scenario_created.v1` / `netplan.solved.v1` / `netplan.approved.v1` / `netplan.executed.v1` / `netplan.outcome_recorded.v1` | create / solve / approve / execute | solver_status、objective_value、variance、policy_version |
| Learning Hub | `learninghub.model_release.v1` | release / rollback | release_type、approval_id、rollback_target、affected_modules、metrics |
| Security | `security.authorization` | (任意) | source_ip、reason、policy_id、tenant_id、data_classification、obligations |

## 4. Decision Audit Timeline & Metadata（共用契約）

所有 Decision Detail 都以 `DecisionAuditTimeline`（component contracts §5.13）+ `AuditMetadata`（§4.16）呈現，**這是跨模組統一契約，業務模組不得自創稽核版面**。

`DecisionAuditTimeline` 固定節點（順序不可改）：

```text
Prediction generated → Recommendation generated → Human review requested
  → Human decision submitted → Execution started → Outcome observed
  → Feedback written to label registry
```

- 每節點顯示對應 `AuditEvent`：actor、`occurred_at`、`outcome`、reason、correlation_id；尚未發生的節點以「待發生」灰態呈現，不得偽造。
- `AuditMetadata` 必含：`feature_snapshot_time`、`model_version`、`policy_version`、actor、`decision_time`、reason、`override_reason`（若有）、`outcome_time`、before/after（覆寫前後值）。
- 可匯出 Evidence（`decision_id` / entity / `model_version` / `feature_snapshot_time` / actor / `decision_time` / `execution_status` / `outcome_status` / `audit_status`）；**匯出本身記 Audit**（`action=export`，達 `RESTRICTED` 須稽核）。

## 5. Routes & Page Jobs

| Route | Page | Primary job | Default density | Main components |
|---|---|---|---|---|
| `/w/audit/decisions` | 決策稽核列表 | 掃描跨模組高風險決策、結果、覆寫與待補證據 | compact | `Table` + status badge + Drawer |
| `/w/audit/decisions/:decisionId` | 決策稽核詳情 | 看完整時間軸、Decision Card、Audit metadata、匯出 | comfortable | `DecisionAuditTimeline` + Decision Card + `EvidencePanel` + `AuditMetadata` |
| `/w/audit/evidence` | Evidence / 補貼證據 | 依條件彙整證據、組補貼證據矩陣、批次匯出 | compact | Subsidy Evidence Matrix + 匯出面板 |
| `/admin/audit` | Audit & Evidence（管理段） | 全租戶高風險決策稽核與 Evidence 匯出（role-gated） | compact | 同上，跨租戶範圍 |

`/admin/audit` 為 role-gated：無 `audit`/`admin` 權限者 Header/switcher 不顯示入口，直接 deep link → 403（見 navigation spec §7.2）。

## 6. 決策稽核列表 Page

- Title：`決策稽核`；Summary：`追溯跨模組高風險決策的模型、核准、執行與結果鏈。`
- Filter Bar（皆進 URL query）：`event_type`、`actor`、`action`、`outcome`(allow/deny/success/failure)、來源模組、`resource`、有無 `override_reason`、date range、selected entity。`compact` 密度（audit log 預設）。
- Table columns：

| Column | Required behavior |
|---|---|
| Decision | `decision_id` / `event_id` + 來源模組 |
| Type | `event_type` + icon |
| Action | `action`；高風險（approve/execute/publish/override/rollback）以警示樣式標示 |
| Actor | `actor` + 角色 |
| Outcome | `outcome`（allow/deny/success/failure）+ icon/pattern；deny/failure 警示 |
| When | `occurred_at` |
| Override | 有 `override_reason` 時顯眼標示 |
| Evidence | 證據完整度（時間軸節點齊全 / 待補） |
| Action | open、檢視時間軸、匯出證據 |

Drawer：顯示決策摘要、目前時間軸節點、actor/outcome 與 next action `開啟稽核詳情`；完整匯出只在 detail 或 evidence 頁執行。

## 7. 決策稽核詳情 Page

固定區段順序（不得重排）：

1. **Summary**：`decision_id`、來源模組、`event_type`、`action`、`actor`、`outcome`、`occurred_at`。
2. **Decision Card**：見 §8。
3. **Audit Timeline**：`DecisionAuditTimeline`（§4），含各節點 `AuditEvent`（actor/at/outcome/reason/correlation_id）。
4. **Audit Metadata**：`AuditMetadata`（§4）—— feature_snapshot_time / model_version / policy_version / actor / decision_time / reason / override_reason / outcome_time / before-after。
5. **Evidence Export**：見 §9。

## 8. Decision Card（必備區塊，缺一不可）

依 component contracts，Decision Card 必含下列區塊（缺一不可）：`Decision Title`、`System Recommendation`、`Human Decision Status`、`Evidence Summary`、`Risk/Confidence`、`Required Approval`、`Primary Action`、`Audit Metadata`。

稽核呈現規則：

- **System Recommendation 與 Human Decision Status 視覺分離**，明確標示「由模型/系統產生」（含 `model_version`/`policy_version`）與「由人核准」（actor/time/reason）。
- `Evidence Summary` 連結至完整時間軸與來源 snapshot；`Risk/Confidence` 用 `EvidencePanel`（§4.18），把限制與低證據等級置頂，不藏 tooltip。
- 若決策被覆寫（`override`），以 explicit comparison 呈現 before/after 與 `override_reason`。

## 9. Evidence Export Section

Evidence 匯出是高敏感動作，UI 必須讓「匯出了什麼、誰匯出、為何匯出」可被再稽核：

- 匯出前顯示將包含的欄位（`decision_id` / entity / `model_version` / `feature_snapshot_time` / actor / `decision_time` / `execution_status` / `outcome_status` / `audit_status`）與資料分級（達 `RESTRICTED`/`HIGHLY_RESTRICTED` 須二次確認）。
- PII 依 `mask_phone`/`mask_email`/`mask_text` 規則遮罩；無解遮權限者只能匯出遮罩版。
- 匯出禁 optimistic、必填 reason、寫後端 Audit（`action=export`）；成功後在頁面追加匯出記錄（actor / reason / exported_at / correlation_id / 範圍）。
- 批次匯出（evidence 頁）顯示筆數、分級分佈與將被遮罩的欄位摘要；不得靜默截斷——若有上限或抽樣，明確標示被排除的範圍。

## 10. Subsidy Evidence Matrix（`/w/audit/evidence`）

補貼證據矩陣把「補貼方案 × 證據要求」組成可逐格驗收的矩陣，供補貼申報與稽核：

- **列**：補貼方案 / 申報項（subsidy program / claim item）；**欄**：所需證據型別（決策核准、執行紀錄、結果觀察、資料快照、模型卡/版本、匯出紀錄）。
- **每格**狀態：齊備 / 待補 / 不適用，齊備格連結到對應 `decision_id` 與 timeline 節點；待補格指出缺哪個節點或欄位。狀態一律 文字 + icon/pattern，不只顏色。
- 以高密度矩陣（`compact`/可切 `presentation`）讓稽核者一眼掃出缺口；提供「整列匯出證據包」與「缺口清單匯出」。
- 補貼證據以既有稽核事件聚合（如 `*.approved`、`*.executed`、`*.outcome_recorded`、`*.dataroom_exported`、`learninghub.model_release`），**不自創新證據語意**；聚合條件與篩選進 URL state，可分享。
- 任何證據格的數值與決策都連回不可變的 `AuditEvent`（`correlation_id`），確保矩陣不是另一份可被竄改的副本。

## 11. Cross-Cutting Rules

### 11.1 Empty / Loading / Error / Permission

| State | Required UI |
|---|---|
| Loading | timeline/table/matrix skeleton；不顯示假節點、假 outcome、假證據齊備 |
| Empty | 無符合條件的決策/證據：說明篩選條件，提供清除 |
| Error | error summary + code + `correlation_id` + retry + timestamp；部分失敗區塊級 degraded |
| Permission | 唯讀或 403；達 `RESTRICTED` 的欄位遮罩，`/admin/audit` role-gated |

### 11.2 High-Risk & Audit Integrity Rules

- Evidence 匯出、覆寫呈現皆禁 optimistic；匯出必填 reason、寫 Audit（`action=export`）。
- 時間軸與矩陣永遠以不可變 `AuditEvent` 為真相來源；尚未發生的節點不得偽造為已完成。
- `outcome=deny`/`failure`、`override_reason`、低證據等級必須顯眼，不得被「整潔版面」掩蓋。

### 11.3 Dense Executive Views & Comparison

- audit log 與矩陣預設 `compact`；詳情 `comfortable`；稽核會議可切 `presentation`（不改語意色）。
- before/after 覆寫、recommendation vs decision、證據齊備度一律 explicit comparison。
- 所有列表/矩陣須有資料表替代與（受權限與遮罩約束的）資料 export。

### 11.4 Accessibility & Responsive

- outcome/override/證據狀態一律 文字 + icon/pattern + tooltip；不以顏色為唯一訊號。
- 表格/矩陣支援 keyboard row/cell focus、`aria-sort`、drawer focus trap。
- `lg+`：完整時間軸 + Decision Card + 矩陣；`md`：單欄、矩陣可橫向捲動並凍結首欄；`sm`：摘要與節點清單，完整矩陣與匯出提示桌機。

## 12. Handoff Checklist

- [ ] 所有 Decision Detail 用統一 `DecisionAuditTimeline` 7 節點與 `AuditMetadata`，業務模組不自創稽核版面。
- [ ] 列表/詳情呈現 `AuditEvent` 全欄位（actor/action/resource/outcome/correlation_id/metadata/occurred_at），高風險 action 與 deny/failure 警示。
- [ ] Decision Card 八區塊齊備，System Recommendation 與 Human Decision Status 視覺分離，覆寫以 before/after + override_reason 呈現。
- [ ] Evidence 匯出顯示欄位與資料分級、依 `mask_*` 遮罩 PII、禁 optimistic、必填 reason、寫 Audit（export）、追加匯出記錄，不靜默截斷。
- [ ] Subsidy Evidence Matrix 以方案×證據型別呈現齊備/待補/不適用，連回 `decision_id` 與 `correlation_id`，聚合既有稽核事件不自創語意。
- [ ] `/admin/audit` role-gated，達 `RESTRICTED` 的檢視/匯出遮罩並稽核。
- [ ] 時間軸/矩陣以不可變 `AuditEvent` 為真相，未發生節點不偽造。
- [ ] 四態、權限、responsive、a11y、URL state、`presentation` 密度全部可逐條驗收。
