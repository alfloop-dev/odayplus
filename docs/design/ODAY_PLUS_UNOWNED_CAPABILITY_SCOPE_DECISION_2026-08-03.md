---
doc_id: ODP-UNOWNED-CAPABILITY-SCOPE-DECISION-2026-08-03
title: Unowned Spec Capability Scope Decision Request
status: decision-required
date: 2026-08-03
language: zh-TW
owner: "Product Lead"
approvers: "Product Lead / Architecture Owner / Program Manager"
decision_due: pending
---

# 規格 MUST 能力但目前無 owner 的範圍決策請求

## 1. 問題陳述

Package 10 把 canonical 前端收斂為 3 個 route（`/operator`、`/intake/[intakeId]`、
`/franchisee`）與 5 個 workspace。**這是已核准的規劃行為，不是缺漏。**

但比對 `ODP-SA-06` 功能需求與 `ODP-UX-03` 畫面規格後，有 5 項在規格中屬
**MUST** 的能力，目前同時滿足三個條件：

1. 不在 canonical runtime 中
2. 不在 26-task 治理 ledger 中
3. 沒有已核准的 deviation 或 ADR

也就是說，它們**既沒有被實作，也沒有被正式排除**。本文件請求對這 5 項做出決策。

## 2. 五項無 owner 能力

### U-1 Model Release Controller UI

| 項目 | 內容 |
|---|---|
| 規格來源 | `UX-SCR-LEARN-003`；`ODP-FR-LH-003`（Backtest / Champion-Challenger / Shadow / Canary / Rollback） |
| 後端狀態 | **已具備**。`apps/api/app/routes/learninghub.py` 有 10 個 endpoint，含 `POST /releases`、`POST /releases/{id}/monitor`、`GET /models/{name}/evidence` |
| 前端狀態 | **完全不存在**。全 repo 搜尋 `promote` / `canary` / `shadow` 在 `apps/web/features/operator/` 無任何操作面實作 |
| 影響 | 模型 promote / canary / rollback 目前**只能靠 API 直呼**，無稽核化 UI。`ODP-FR-GOV-009`（高風險功能需 feature flag、人工核准、canary）在 UI 層無法落實 |
| 建議 | **排入下一個 release**。這是四個核心模型上線後的必要操作面，缺它會讓 model governance 停留在工程手動 |

### U-2 User and Role Management UI

| 項目 | 內容 |
|---|---|
| 規格來源 | `UX-SCR-ADMIN-001`；`ODP-FR-OPS-003`（依 Tenant/Brand/Region/Store/Role/Attribute 控制資料） |
| 後端狀態 | RBAC/ABAC 已具備；`ODP_AUTH_PRINCIPAL_MAP` 以 secret 形式維護 |
| 前端狀態 | 僅 `govern/系統狀態` 有**唯讀**角色列，無任何 CRUD |
| 影響 | 角色與權限異動目前需改 Secret Manager 並重新部署，無自助管理、無異動稽核軌跡 |
| 建議 | **排入下一個 release**，或正式決議「權限由 IdP 端管理」並補 ADR |

### U-3 Feature Flag Management UI

| 項目 | 內容 |
|---|---|
| 規格來源 | `UX-SCR-ADMIN-002`；`ODP-FR-SHARED-004` |
| 規格原文 | 「未啟用 Feature Flag 時 UI、API、Job 均不得執行該功能」 |
| 後端狀態 | 僅 `apps/api/oday_api/security/` 有 feature flag 判定 |
| 前端狀態 | **完全不存在** |
| 影響 | **這是 `ODP-FR-GOV-009` 高風險控制的核心機制**。PriceOps、AdLift、NetPlan、模型發布、決策政策變更都指定用 feature flag 控制啟用範圍。目前無法在不重新部署的情況下切換 |
| 建議 | **P1，優先於 U-1/U-2**。沒有它，高風險功能的 canary/kill-switch 無法在營運時段執行 |

### U-4 通知實際投遞（Email / 站內）

| 項目 | 內容 |
|---|---|
| 規格來源 | `ODP-FR-SHARED-006`（站內、Email、Webhook 或後續擴充通道） |
| 現況 | `modules/notifications/infrastructure/adapters.py` 只有 `ConsoleNotificationAdapter` 與 `OnCallNotificationAdapter`（webhook）。全 repo 搜尋 `smtp` / `sendgrid` / `ses` / `send_email` **零命中** |
| 資料模型 | `domain/models.py` 的 `channels` 預設 `["email"]`、`channel = "email"`——**欄位存在但沒有實際投遞實作** |
| 影響 | 規格要求「任務指派、逾時、核准、失敗、回滾均可通知相關 Actor」目前只有 webhook 一條路。指派給人的通知實際上不會送達 |
| 建議 | **P1**。UAT 需要 6 種角色實際收到任務指派通知，否則 `ODP-PLAN-UAT-SIGNOFF-001` 難以真實執行 |

### U-5 任務附件

| 項目 | 內容 |
|---|---|
| 規格來源 | `ODP-FR-OPS-002`（任務、指派、留言、附件、核准、升級與通知） |
| 現況 | 全 repo 搜尋 `attachment` 在 `modules/`、`apps/api/` **零命中**。留言、指派、核准、升級皆有實作，只有附件缺 |
| 影響 | 現勘照片、租約掃描、設備故障影像等現場證據無法掛在任務上。對展店與工務流程影響明顯 |
| 建議 | **P2**，但需明確決策。若決定不做，須說明現場證據改走哪條路徑（例如 AVM Data Room 或外部系統） |

## 3. 決策選項

對每一項，請在下列三者擇一：

| 選項 | 意義 | 後續動作 |
|---|---|---|
| **A. 排入下一個 release** | 承認是 MUST，補實作 | 建立 execution task packet，指派 owner/reviewer，納入 RTM |
| **B. 正式 deviation** | 本期不做，但保留需求 | 依 `ODP-SA-06 §5` 提交 Deviation，記錄 owner 與到期日 |
| **C. 範圍變更 + ADR** | 需求本身改變（例如改由外部系統承擔） | 依 `ODP-00-04` 提 ADR，更新 `ODP-SA-06` 與 `ODP-UX-03` |

**不可接受的狀態**：維持現狀（既未實作、也未正式排除）。這會讓
`ODP-PLAN-FINAL-GATE-AUDIT-001` 在重跑 84-row RTM 時出現無法歸類的缺口。

## 4. 建議優先序

| 序 | 能力 | 建議 | 理由 |
|---|---|---|---|
| 1 | **U-3 Feature Flag 管理** | A | 高風險功能 kill-switch 的前提，且 `FR-SHARED-004` 明文要求 |
| 2 | **U-4 通知投遞** | A | UAT 6 角色簽核需要真實通知送達 |
| 3 | **U-1 Model Release Controller** | A | 模型上線後的必要操作面，後端已就緒，工作量相對可控 |
| 4 | **U-2 User/Role 管理** | A 或 C | 若決定由 IdP 承擔則走 C |
| 5 | **U-5 任務附件** | A 或 B | 影響現場流程但不擋 Gate |

## 5. 其他已收斂但較規格薄的能力（本文件不請求決策，僅列管）

以下 12 項有實作但深度不及 `ODP-UX-03`，建議在 U-1~U-5 決策後一併排期，
不需要獨立 deviation：

`UX-SCR-EXP-001` 前端 geocoder 地址搜尋未接、`INT-001/002` Intervention
Inbox/Detail、`PRICE-002` Pricing Simulation 互動、`AD-002` Lift Report、
`AVM-001/002` AVM Queue 與 Valuation Card（Data Room 無專屬旅程）、
`NET-001/002` Scenario Builder 參數輸入與 Solver Result binding/infeasibility 明細、
`LEARN-001/002` Data Quality Center 與 Model Registry 由唯讀升為可操作、
`FR-LST-001` Excel/xlsx 直接匯入。

## 6. 決策記錄

| 能力 | 決策 | 決策者 | 日期 | 後續 task id |
|---|---|---|---|---|
| U-1 Model Release Controller UI | _pending_ | | | |
| U-2 User and Role Management UI | _pending_ | | | |
| U-3 Feature Flag Management UI | _pending_ | | | |
| U-4 通知實際投遞 | _pending_ | | | |
| U-5 任務附件 | _pending_ | | | |
