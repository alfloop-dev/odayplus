---
doc_id: ODP-R0-SCREEN-INVENTORY
title: "ODay Plus R0 Screen Inventory"
version: 0.1.0
status: draft
document_class: ux-blueprint
project: ODay Plus
language: zh-TW
updated_at: 2026-06-27
owner: "Product Design / Frontend"
approvers: "Product Lead / Frontend Lead"
content_format: markdown
source_documents:
  - ODP-UX-01_INFORMATION_ARCHITECTURE_AND_NAVIGATION.md
  - ODP-UX-03_SCREEN_AND_INTERACTION_SPECIFICATION.md
  - ODP-UX-05_FRONTEND_TECHNICAL_DESIGN.md
  - ODP-MOD-11_OPSBOARD.md
related_documents:
  - docs/design/ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md
  - docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md
  - docs/design/ODAY_PLUS_OPSBOARD_SHELL_BLUEPRINT.md
  - docs/design/ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md
---

# ODay Plus R0 Screen Inventory

## 1. Purpose & Scope

本文件逐條列出 **R0 OpsBoard 殼層擁有的每一個畫面（route）**，並對每條 route 給出：**用途、版面區域（layout regions）、空/載入/錯誤狀態、角色感知行為、responsive 行為、使用元件**。它是 `ODP-R0-004`（OpsBoard shell 實作）的逐頁來源，目標是讓實作 worker **不需自行發明任何 UX**。

範圍邊界（承殼層 blueprint §2）：

- **In scope（R0 殼層擁有）**：Home/Today、Workspace Home、Task Center、Notification Center、Global Search Results、Settings、Admin sections、系統狀態頁（auth/403/404/500/offline/maintenance）。
- **Out of scope（模組擁有，僅定義插槽契約）**：各 workspace 內的模組工作頁（HeatZone、SiteScore、ForecastOps … Level 2/3）。本文件 §13 只定義「模組頁插進殼層時必須遵守的插槽契約」，不規格化模組頁內容。

逐頁規格的共同詞彙（四態、角色、region、responsive）皆引用殼層 blueprint 與 navigation spec，不在此重新定義。

### 1.1 共同四態（每條 route 必備）

| 態 | 要求（細節見殼層 blueprint §9） |
|---|---|
| Loading | content 區 skeleton；chrome 先渲染；大型 Job 不顯假百分比 |
| Empty | `EmptyState`（title + description + nextActions + docLink），不只「沒有資料」 |
| Error | 摘要 + 錯誤代碼 + 可重試 + 下一步 + `correlation_id` + 時間；degraded 隔離；不只 Toast |
| Permission | 無權限不渲染入口；deep link 受限 → 403；可讀不可寫顯示唯讀摘要無操作鈕 |

### 1.2 Route 總表

| # | Route | 畫面 | Level | 主要元件 | 預設密度 |
|---|---|---|---|---|---|
| S1 | `/`, `/home` | Home / Today | L0 | Card grid + ApprovalPanel + AlertChip | comfortable |
| S2 | `/w/:workspace` | Workspace Home | L1 | Card grid（workspace 化的 S1） | comfortable |
| S3 | `/tasks`, `/tasks/:id` | Task Center | L0 | Table + Drawer | compact |
| S4 | `/notifications` | Notification Center | L0 | Table/List + tabs + Drawer | compact |
| S5 | `/search?q=` | Global Search Results | L0 | Grouped list | comfortable |
| S6 | `/settings/:section` | Settings | L0 | Form / Tabs | comfortable |
| S7 | `/admin/:section` | Admin Shell | L0(gated) | Table / Form / Audit timeline | compact |
| S8 | `/login` 等 | Auth | — | Auth form | — |
| S9 | `/403` | Forbidden | — | EmptyState 變體 | — |
| S10 | `/404` | Not Found | — | EmptyState 變體 | — |
| S11 | `/500` | Server Error | — | Error page | — |
| S12 | `/offline` | Offline / Degraded | — | Status page | — |
| S13 | `/maintenance` | Maintenance | — | Status page | — |
| — | `*` slot | Module Page 插槽契約 | L2/L3 | 見 §13 | 視頁面 |

---

## 2. S1 — Home / Today（`/`, `/home`）

- **用途**：登入後第一畫面；角色化的營運待辦與現況控制台（decision-first）。完整版面與四態見殼層 blueprint §7，本節為 route 級摘要與差異點。
- **Layout regions**：R0 Header + R1 Sidebar（預設 workspace）+ R2 Page Header（`今日營運` + 待辦/SLA/資料新鮮度摘要 + last updated）+ R3b Content（A 我的佇列 / B 待我核准 / C 警示 / D 模型·資料健康 / E 工作區捷徑）。無 R3a Filter Bar。Drawer：點 B 的核准項或 C 的警示可開 detail drawer。
- **角色感知**：內容依角色解析（殼層 blueprint §7.4 矩陣）；無權限區塊不渲染；加盟主版以「我的門市」為主、無核准區。
- **四態**：見殼層 blueprint §7.3（區塊級隔離；empty 走 EmptyState；高風險核准不在 Home optimistic）。
- **Responsive**：`lg`+ 雙欄四象限；`md` 兩欄堆疊；`sm` 單欄固定序 A→B→C→D→E，每區塊「查看全部 →」。
- **元件**：`Card`(Summary/Decision/Risk/Model/Task)、`ApprovalPanel`(摘要入口)、`AlertChip`、`FourLightBadge`、`DataStatusBadge`、`ModelVersionBadge`、`EmptyState`。

## 3. S2 — Workspace Home（`/w/:workspace`）

- **用途**：切換到某 workspace 後的落點；等同 S1 但**範圍收斂到該 workspace**（只顯示該領域的佇列/核准/警示/健康/捷徑）。
- **Layout regions**：同 S1，Sidebar 為該 workspace 導覽，Page Header 標題為 workspace 名（`展店 · 今日`）。
- **角色感知**：僅當使用者對該 workspace 有權限；無權限 → 403。可讀不可寫者只見唯讀摘要區塊。
- **四態**：同 S1（區塊級）。Empty：該 workspace 暫無待辦時 EmptyState 引導至模組頁（如「瀏覽 HeatZone Radar →」）。
- **Responsive**：同 S1。
- **元件**：同 S1。

## 4. S3 — Task Center（`/tasks`, `/tasks/:id`）

- **用途**：集中所有指派給我 / 待我核准 / 我發起待回 / 我關注的待辦（navigation spec §5.3）。
- **Layout regions**：R2 Page Header（`任務中心` + 待辦摘要）+ R3a Filter/Toolbar（tab：指派我/待核准/我發起/已完成；filter；saved view；density）+ R3b `Table`（compact）+ R4 Drawer（`/tasks/:id` 任務 detail，可上一筆/下一筆、deep link）。
- **Table 必備欄位**：`entity_name`、`task_type`、`status`、`priority`、`owner`、`due/SLA`、`updated_at`、`primary_action`。逾 SLA 同時用文字 + 顏色 + icon。
- **角色感知**：只見有權限的任務；無核准權者「待核准」tab 空或隱藏；敏感欄位依 field permission 遮罩。
- **四態**：Loading→表格 skeleton；Empty→`EmptyState`（「目前沒有待辦」+「瀏覽工作區 →」）；Error→inline 錯誤列 + 代碼 + 重試；drawer 載入獨立。
- **高風險**：drawer 內核准走完整 `ApprovalPanel`，**不 optimistic**，回傳 `decision_id`，計數回授 Header。
- **Responsive**：`lg`+ 表格 + 並排 drawer；`md` filter 收按鈕、drawer overlay；`sm` 表格降為卡片列表、drawer 全幅 sheet、批次走 action sheet。
- **元件**：`Table`、`Drawer`、`Toolbar/FilterBar`、`ApprovalPanel`、`Badge`、`AlertChip`、`EmptyState`。

## 5. S4 — Notification Center（`/notifications`）

- **用途**：警示（四燈）、核准請求、Job 結果、系統公告的集中入口（navigation spec §5.4）。
- **Layout regions**：R2 Page Header（`通知` + 未讀數）+ R3a Toolbar（tab：全部/警示/核准/Job/系統；標記已讀；filter）+ R3b List/Table（compact）+ R4 Drawer（通知 detail / 來源預覽）。
- **每筆**：tone（顏色 + 文字 + icon/pattern）、來源實體、時間、已讀/未讀、`onClick`→來源 detail。
- **角色感知**：只見與我相關或我有權限來源的通知；加盟主只見自己門市相關。
- **四態**：Loading→列表 skeleton；Empty→「目前沒有新通知」+ 可調整通知偏好連結；Error→inline + 代碼；Job 失敗類通知連到對應 Job 詳情。
- **規則**：critical alert **不閃爍**；讀取後計數以後端為準（非 optimistic）；通知是入口，不取代頁面 inline 狀態。
- **Responsive**：`lg`+ 列表 + drawer；`sm` 單欄卡片、drawer 全幅。
- **元件**：`Table`/list、`AlertChip`、`FourLightBadge`、`Drawer`、`Badge`、`EmptyState`、`Toast`（標記已讀回饋）。

## 6. S5 — Global Search Results（`/search?q=`）

- **用途**：跨 workspace 搜尋頁面與實體的完整結果頁（命令面板的「查看全部結果」落點）。
- **Layout regions**：R2 Page Header（`搜尋：「<q>」` + 結果數）+ R3a Toolbar（依型別 filter、workspace filter）+ R3b 分組結果列表（依實體型別分組：門市/商圈/Listing/Candidate/Plan/Model/頁面…）。
- **每筆**：型別 icon + 名稱 + workspace + 狀態 chip + 最後更新；`onClick` deep link 到該實體（跨 workspace 時 URL 一次到位）。
- **角色感知**：無權限實體**不出現在結果**（不是顯示後鎖定）。
- **四態**：Loading→分組 skeleton；Empty→「找不到符合『<q>』的結果」+ 建議（檢查拼字 / 換關鍵字 / 瀏覽工作區）；Error→inline + 代碼 + 重試；無 query→近期瀏覽 + 熱門入口。
- **Responsive**：`lg`+ 分組多欄；`sm` 單欄分組可折疊。
- **元件**：grouped list、`Badge`、`AlertChip`、`EmptyState`、`CommandPalette`（共用搜尋來源）。

## 7. S6 — Settings（`/settings/:section`）

- **用途**：使用者個人偏好與帳號設定。
- **Sections**：`profile`（個資/角色檢視）、`appearance`（theme / density 偏好）、`notifications`（通知偏好）、`security`（登入/工作階段）、`shortcuts`（鍵盤快捷開關）。
- **Layout regions**：R2 Page Header（`設定`）+ R3b：左側 section `Tabs`/nav + 右側 `Form`。
- **角色感知**：只顯示自己可改的偏好；角色/權限為唯讀展示（變更走 Admin）。
- **四態**：Loading→表單 skeleton；Empty 不適用；Error→欄位級錯誤 + 整體摘要，失敗保留輸入；儲存成功 Toast。
- **規則**：偏好變更（theme/density/通知）即時生效並寫 user pref；安全相關（登出其他工作階段）二次確認。
- **Responsive**：`lg`+ 左 nav 右表單；`sm` section 為上方下拉、表單單欄。
- **元件**：`Tabs`、`Form`、`Toast`、`Button`、`Badge`（角色唯讀）。

## 8. S7 — Admin Shell（`/admin/:section`）

- **用途**：管理殼層（navigation spec §7）。R0 提供導覽與 route 結構，內容深度隨 release 擴充。
- **Sections**：`users`（使用者/角色/權限）、`workspaces`（啟用/導覽配置）、`environment`（環境/feature flags 檢視）、`audit`（高風險決策稽核 + Evidence 匯出）、`health`（服務/Job/queue 概況）。
- **Layout regions**：R1 Sidebar 為 admin 導覽 + R2 Page Header + R3a Toolbar（filter/search/batch）+ R3b `Table`/`Form`/`DecisionAuditTimeline` + R4 Drawer（detail）。
- **角色感知（硬規則）**：非 admin/對應角色者 **Header/switcher 不顯示 Admin 入口**；直接 deep link → 403。各 section 再依細權限分檢視/編輯。敏感清單（個資/權限）依 field permission 遮罩。
- **四態**：標準四態；audit 匯出大型資料走 Job progress（非假百分比）。
- **高風險（不得 optimistic，須後端 Audit）**：權限變更、workspace 啟用、feature flag、Evidence 匯出 → 二次確認 + 理由 + Audit + watermark（匯出）。
- **Responsive**：`lg`+ 完整；`md` 可檢視/輕量管理；`sm` **僅檢視**，管理動作標示「請於桌機操作」。
- **元件**：`Table`、`Form`(Approval/Policy)、`DecisionAuditTimeline`、`AuditMetadata`、`Drawer`、`Modal`(確認)、`Badge`。

## 9. S8 — Auth（`/login`, callback, session expired）

- **用途**：登入 / SSO callback / 工作階段過期重新驗證。
- **Layout regions**：**無殼層 chrome**（無 Header/Sidebar），置中 auth 卡片。
- **角色感知**：未驗證者所有受保護 route 導向此頁，登入後回原 deep link（保留 `returnTo`）。
- **四態**：Loading→驗證中；Error→明確錯誤（帳密錯/SSO 失敗/逾時）+ 代碼 + 重試/聯絡管道，不洩漏帳號是否存在。
- **Responsive**：所有尺寸置中單欄。
- **元件**：`Form`、`Button`、`Toast`、`EmptyState`（session expired 變體）。

## 10. S9–S11 — 權限/找不到/伺服器錯誤

| Route | 用途 | 內容 | chrome |
|---|---|---|---|
| `/403` Forbidden | 有登入但無權限 | 說明缺少的權限類別 + 申請/聯絡路徑 + 回 Home；**不洩漏受限內容** | 保留 Header/Sidebar（可導覽到有權限處） |
| `/404` Not Found | route/實體不存在 | 說明 + 搜尋入口 + 回 Home / 上一頁 | 保留 chrome |
| `/500` Server Error | 後端錯誤 | 錯誤摘要 + `correlation_id` + 時間 + 重試 + 聯絡；不顯示 stack | 可保留 chrome；嚴重時降級為獨立頁 |

- 三者皆為 `EmptyState`/Error page 變體，文案走 visual system §8.4（發生什麼/可能原因/下一步/代碼/correlation_id）。

## 11. S12–S13 — Offline / Maintenance

- **S12 Offline / Degraded（`/offline` 或殼層 degraded 模式）**：header `OFFLINE` chip；可寫高風險動作禁用 + tooltip；唯讀內容可從 cache 並標示快取時間；恢復連線自動重整提示。
- **S13 Maintenance（`/maintenance`）**：計畫性維護全頁狀態；預計恢復時間 + 狀態頁連結；無 chrome 或最小 chrome。
- **四態**：本身即狀態頁；Loading 期間顯示重試。
- **元件**：status page、`AlertChip`、`Button`(重試)。

## 12. 角色 × 畫面可見性矩陣（範例）

`●`=可操作 `○`=唯讀 `—`=不可見（不渲染入口，deep link→403）。實際以後端權限為準。

| 畫面 | 展店審查 | 營運主管 | 定價 | 財務法務 | AI/資料 | 加盟主 | 稽核 | Admin |
|---|---|---|---|---|---|---|---|---|
| S1 Home | ● | ● | ● | ● | ● | ●(門市版) | ● | ● |
| S3 Tasks | ● | ● | ● | ● | ● | ●(限自己) | ○ | ● |
| S4 Notifications | ● | ● | ● | ● | ● | ●(限門市) | ● | ● |
| S5 Search | ● | ● | ● | ● | ● | ●(限門市範圍) | ○ | ● |
| S6 Settings | ● | ● | ● | ● | ● | ● | ● | ● |
| S7 Admin | — | — | — | — | ○(health) | — | ○(audit) | ● |
| 展店 workspace | ● | ○ | — | — | ○ | — | ○ | ○ |
| 定價 workspace | — | ○ | ● | — | ○ | — | ○ | ○ |
| AVM workspace | — | — | — | ● | ○ | — | ○ | ○ |

---

## 13. Module Page Slot Contract（§out-of-scope 頁面的插槽契約）

R0 殼層不實作模組頁內容，但**必須保證**任何模組頁插入殼層時遵守以下契約（讓後續模組 worker 有穩定插槽）：

1. **Region 合約**：模組頁提供 Page Header 內容（title/summary/status/actions/breadcrumb/lastUpdated）、可選 Filter Bar、Content、可選 Drawer。殼層提供 R0/R1/R5。
2. **七層資訊層級**（Detail 頁）：Summary → Status → Evidence → Recommendation → Decision → Execution/Result → Version/Audit，順序固定不得重排（visual system §4.3）。
3. **四態**：模組頁的每個 content slot 自帶 loading/empty/error/(stale)，殼層不替模組頁假裝資料。
4. **URL 狀態**：模組頁的 tab/filter/sort/page/選中/drawer 走殼層的 URL 契約（navigation spec §6）。
5. **高風險動作**：核准/override/export/price/netplan/valuation/model release/rollback/data-quality override 不得 optimistic，須觸發後端 Audit（component contracts §6.3）。
6. **權限**：無權限不渲染操作；可讀不可寫顯示唯讀；deep link 受限→403。
7. **不確定性**：預測/估值頁顯示 P10/P50/P90 + confidence + data freshness + model version（visual system §10.3）。

> 模組頁的逐欄/逐互動規格由各模組 UX 規格（`ODP-UX-03` 系列與各 `ODP-MOD-*`）擁有，不在本 R0 inventory 重複。

---

## 14. Handoff Checklist（screen inventory）

- [ ] S1–S13 每條 route 都有：用途、layout regions、四態、角色感知、responsive、元件清單。
- [ ] 每條 route 的四態實作齊備（loading skeleton / EmptyState+nextActions / error+代碼+correlation_id / permission）。
- [ ] Home（S1）與 Workspace Home（S2）角色化、decision-first、區塊級隔離、高風險不 optimistic。
- [ ] Task/Notification Center（S3/S4）：Table+Drawer+tab、計數回授 Header、critical alert 不閃爍、敏感欄位遮罩。
- [ ] Search（S5）權限過濾結果、deep link、空/錯誤文案完整。
- [ ] Settings（S6）偏好即時生效、Admin（S7）role-gated + 高風險走 Audit + sm 僅檢視。
- [ ] 系統頁（S8–S13）：auth 保留 returnTo、403 不洩漏內容、404/500/offline/maintenance 文案含代碼/下一步。
- [ ] 角色×畫面矩陣落實為實際權限過濾（無權限不渲染入口，deep link→403）。
- [ ] Module Page Slot Contract（§13）成立：模組頁能插入殼層並遵守 region/七層/四態/URL/高風險/權限/不確定性。
- [ ] 全部走 semantic token、既有元件契約、canonical 狀態碼；a11y + 視覺回歸 + Playwright smoke 就緒。

---

## 15. 驗收條件

本文件作為 R0 Design Gate 交付物，需滿足：

- 列出 R0 殼層擁有的每一條 route（S1–S13）並逐條給出用途、layout regions、空/載入/錯誤狀態、角色感知行為、responsive 行為與使用元件。
- 提供角色 × 畫面可見性矩陣，並對齊「無權限不渲染、deep link 受限→403」。
- 定義 Module Page Slot Contract，使後續模組頁可插入殼層而不破壞 region/七層資訊層級/四態/URL/高風險/權限/不確定性規則。
- 提供可逐條檢查的 handoff checklist。
- 與殼層 blueprint、navigation spec 及 UXD-001 三份文件互相引用且不矛盾。
- 可被 `ODP-R0-004` 直接據以實作每一個 R0 畫面，無需自行發明 UX。
