---
doc_id: ODP-R0-NAVIGATION-AND-WORKFLOW-SPEC
title: "ODay Plus Navigation and Workflow Spec"
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
  - docs/design/ODAY_PLUS_R0_SCREEN_INVENTORY.md
---

# ODay Plus Navigation and Workflow Spec

## 1. Purpose & How to Read

本文件定義 ODay Plus OpsBoard 的 **資訊架構（IA）、導覽、路由、URL 狀態、工作區切換、全域搜尋、任務中心、通知中心、命令面板、管理殼層**，以及把它們串起來的 **跨工作區工作流程**。它是 `ODAY_PLUS_OPSBOARD_SHELL_BLUEPRINT.md` 的姊妹文件：殼層 blueprint 講「畫面長什麼樣」，本文件講「畫面之間怎麼連、URL 怎麼編、流程怎麼走」。

不重複定義：

- 殼層 region 版面、首屏、responsive → 見 `ODAY_PLUS_OPSBOARD_SHELL_BLUEPRINT.md`。
- 逐頁規格（每條 route 的四態與角色行為）→ 見 `ODAY_PLUS_R0_SCREEN_INVENTORY.md`。
- 元件契約 / token / 視覺規則 → 見對應 UXD-001 文件。

衝突解析同殼層 blueprint §1：值看 token、契約看 component、規則看 visual system，導覽與路由看本文件。

---

## 2. Information Architecture

### 2.1 三層 IA（normative）

```text
Level 0  Global Chrome      跨工作區：Home/Today · Search · Tasks · Notifications · Settings · Admin
Level 1  Workspace          一個角色/領域的工作場域（展店、營運、定價、財務、AI/資料、加盟主、稽核）
Level 2  Module Page        workspace 內的具體工作頁（Listing Inbox、SiteScore Report …）
Level 3  Entity Detail      單一實體的 Detail（含 tabs / drawer / deep link）
```

- Level 0 由 OpsBoard 殼層擁有（本文件 + 殼層 blueprint）。
- Level 1/2 的清單在本文件 §4 定義 R0 範圍；Level 2/3 的逐頁內容由各模組 UX 規格擁有，殼層只提供插槽與導覽。

### 2.2 導覽原則（承 visual system §3）

1. **Decision-first 導覽**：導覽以「我要完成的決策任務」為中心組織，不是以資料表清單為中心。
2. **角色決定可見性**：workspace 與導覽項依後端權限過濾；無權限不渲染，可讀不可寫顯示唯讀標記。
3. **三條全域捷徑永遠可達**：全域搜尋、任務中心、通知中心在任何頁面都從 Global Header 直達。
4. **可預測的 URL**：每個畫面狀態都有穩定、可分享、可書籤的 URL（§6）。

---

## 3. Workspace Model & Switching

### 3.1 Workspace 是什麼

Workspace 是「一組相關模組頁面 + 一個 Sidebar 導覽 + 一個預設 Home」的集合，對應一個角色/領域的工作場域。切換 workspace = 換 Sidebar（R1）與預設落點，**不換**殼層骨架。

### 3.2 R0 Workspace 清單

對應 `docs/architecture/ODAY_PLUS_EXECUTION_BASELINE.md §3` 的模組集合，R0 殼層需能承載下列 workspace（實際啟用順序由 phase/release 計畫控制，但殼層導覽結構必須就緒）：

| Workspace key | 名稱 | 主要模組頁（Level 2，內容由各模組 UX 擁有） | 典型角色 |
|---|---|---|---|
| `expansion` | 展店 Expansion | HeatZone Radar · Listing Inbox · Candidate Sites · SiteScore Reports | 展店審查 |
| `operations` | 營運 Operations | ForecastOps · Alerts · InterventionOps | 營運主管 |
| `pricing` | 定價 Pricing | PriceOps Plans · AdLift Reports | 定價/行銷 |
| `dealroom` | 財務／交易 DealRoom | DealRoomAVM · Valuation · DataRoom | 財務法務 |
| `network` | 網絡規劃 NetPlan | NetPlan Scenarios · Solver Runs | 營運/策略 |
| `ai` | AI／資料 | Model Registry · Releases · Data Quality · Learning Hub | AI/資料團隊 |
| `franchise` | 加盟主 | 我的門市 · 我的任務 · 我的通知 | 加盟主 |
| `audit` | 稽核 | Decision Audit · Evidence Export | 稽核 |

> 各 workspace 的模組頁清單是**導覽結構**，不是本任務要實作的頁面內容。R0 殼層保證導覽項可被權限過濾並正確高亮；頁面內容由各模組 worker 依其 UX 規格實作。

### 3.3 切換行為（normative）

- Workspace Switcher 在 Global Header，顯示當前 workspace，下拉列出**有權限**的 workspace（無權限不列）。
- 切換 workspace → 導向該 workspace 的 Home（Level 1 落點），URL segment 改變（§6）。
- 切換**不保留**前一個 workspace 的頁面 filter 狀態（不同領域），但**保留**全域偏好（density / theme / sidebar collapsed）。
- 使用者有「預設 workspace」偏好（後端 user pref）；登入後 `/` 解析到該 workspace 的 Home；無設定時取第一個有權限 workspace。

---

## 4. Sidebar Navigation Map（Level 1 → Level 2）

Sidebar（R1）依當前 workspace 渲染。範例（`expansion`）：

```text
WORKSPACE 展店
  群組 A · 探勘
    Today / Home            /
    HeatZone Radar          /w/expansion/heatzone
  群組 B · 物件
    Listing Inbox           /w/expansion/listings        (badge: 待處理數)
    Candidate Sites         /w/expansion/candidates
  群組 C · 評分與決策
    SiteScore Reports       /w/expansion/sitescore
  ──────────  (全域，固定於底)
    Tasks                   /tasks
    Notifications           /notifications
```

規則：

- 第一層為任務群組標題（不可點），第二層為可導覽頁。
- 當頁高亮 `aria-current="page"`；父群組展開。
- 全域捷徑（Tasks / Notifications）固定於 Sidebar 底部，於所有 workspace 一致。
- 各 workspace 的完整 Sidebar map 由該 workspace owner 維護；本文件提供結構契約與 `expansion` 範例。

---

## 5. Global Surfaces（Level 0）

四個跨工作區表面，皆從 Global Header 直達，逐頁規格見 R0 screen inventory。

### 5.1 Global Search

- 入口：Header search 框 / icon，或 `Cmd/Ctrl+K` 開 Command Palette。
- 範圍：跨 workspace 搜尋頁面、實體（門市、商圈、Listing、Candidate、Plan、Model …）、與最近瀏覽。
- 結果分類顯示（依實體型別分組），每筆顯示型別 + 名稱 + workspace + 狀態 chip。
- 權限過濾：無權限的實體不出現在結果。
- 結果可 deep link（`/search?q=...`）；Enter 直接前往第一筆或進入完整 Search Results 頁。

### 5.2 Command Palette（`Cmd/Ctrl+K`）

- 用途：搜尋頁面/實體、建立任務、跳轉最近瀏覽、執行**有權限**的快速動作。
- **不列出無權限動作**（`ODAY_PLUS_COMPONENT_CONTRACTS.md §4.11`）。
- z-index 最上層（`z.command-palette`）。
- 鍵盤優先：上下選擇、Enter 執行、Esc 關閉；輸入即時過濾。
- 動作分類：Navigate（去某頁/實體）、Create（建任務）、Action（有權限的快速操作，如「指派任務」），Recent（最近瀏覽）。

### 5.3 Task Center

- 用途：集中「指派給我 / 待我核准 / 我發起待回 / 我關注」的待辦。
- 列表為主（`Table`，compact 密度預設），欄位：`entity_name` / `task_type` / `status` / `priority` / `owner` / `due/SLA` / `updated_at` / `primary_action`。
- 點選列開 Right Drawer 快速查看（task detail），可上一筆/下一筆、deep link；高風險核准在 drawer 仍走完整 ApprovalPanel，不 optimistic。
- filter / sort / tab（指派我/待核准/我發起/已完成）與 URL 同步。
- 計數回授到 Header 的 Tasks badge（§殼層 §4.2）。

### 5.4 Notification Center

- 用途：警示（四燈）、核准請求、Job 結果（成功/失敗）、系統公告。
- 列表 + 分類 tab（全部/警示/核准/Job/系統）；已讀/未讀狀態；批次標記已讀。
- 每筆：tone（顏色 + 文字 + icon）、來源實體、時間、`onClick` → 來源 detail。
- **不閃爍** critical alert；通知不取代頁面內 inline 狀態，只是入口。
- 計數回授到 Header 的 Bell badge；讀取後計數以後端為準（非 optimistic）。

---

## 6. Routing & URL State Contract（normative）

### 6.1 URL 結構

```text
/                                  → 解析到使用者預設 workspace 的 Home
/home                              → 同上（顯式）
/w/:workspace                      → workspace Home（Level 1 落點）
/w/:workspace/:module              → module 列表/工作頁（Level 2）
/w/:workspace/:module/:entityId    → entity Detail（Level 3）
/tasks            /tasks/:taskId   → Task Center（detail 走 drawer，URL 反映選中）
/notifications                     → Notification Center
/search?q=...                      → Global Search Results
/settings/:section                 → 使用者設定
/admin/:section                    → 管理殼層（role-gated）
/login  /403  /404  /500  /offline /maintenance  → 系統狀態頁
```

### 6.2 URL = 狀態（硬規則，承 `ODP-UX-05 §7.3`）

可被分享/書籤的狀態一律進 URL，不只存元件 state：

- workspace、module、entity（path segment）。
- 列表狀態：`tab`、`filter`、`sort`、`page`、`pageSize`、`q`、`dateRange`（query）。
- 選中與 drawer：選中 entity 與 drawer 開啟以 query/segment 反映，可分享。
- 不進 URL：sidebar collapsed、density、theme（屬 device/user 偏好）。

要求：

- 重新整理頁面能還原同一畫面狀態（含 drawer 開啟與選中項）。
- 複製 URL 給有權限同事，看到相同畫面狀態（無權限則導 403）。
- 瀏覽器上一頁/下一頁符合直覺（不破壞 filter 狀態）。

### 6.3 Breadcrumb 模型

- Detail 頁**必須**有 breadcrumb：`Workspace › Module › Entity`，每段可點。
- Breadcrumb 與 URL segment 一致；最後一段為當前頁（不可點）。
- mobile（`sm`）breadcrumb 收為「‹ 返回」回上一層。

---

## 7. Admin Shell（管理殼層）

R0 需提供管理殼層的導覽與 route 結構（內容深度可隨 release 擴充，但結構與權限門檻 R0 就緒）。

### 7.1 Admin sections（`/admin/:section`）

| Section | route | 內容 | 門檻 |
|---|---|---|---|
| Users & Roles | `/admin/users` | 使用者、角色、權限指派（檢視/編輯依權限） | admin |
| Workspaces | `/admin/workspaces` | workspace 啟用、導覽配置 | admin |
| Environment | `/admin/environment` | 環境資訊、feature flags（檢視為主） | admin / ops |
| Audit & Evidence | `/admin/audit` | 高風險決策稽核、Evidence 匯出 | audit / admin |
| System Health | `/admin/health` | 服務狀態、Job/queue 概況 | ops / admin |

### 7.2 規則

- Admin 是 role-gated workspace：無權限者 Header/switcher **不顯示** Admin 入口；直接 deep link → 403。
- 管理動作多屬高風險（權限變更、feature flag、Evidence 匯出）：**不得 optimistic**，須二次確認 + 理由 + 後端 Audit（visual system §10.3、`ODAY_PLUS_COMPONENT_CONTRACTS.md §6.3`）。
- 敏感清單（使用者個資、權限）依 field permission 遮罩。

---

## 8. Cross-Workspace Workflows

導覽不只是頁面樹，還要支撐跨工作區的決策流程。以下流程的每一步都對應一個可 deep-link 的畫面狀態，且 prediction / recommendation / human decision / execution / outcome 必須在畫面上分離（visual system §3）。

### 8.1 展店決策流（expansion）

```text
HeatZone Radar (找熱區) → Listing Inbox (待解析物件) → Candidate Site (地理編碼/可行性)
→ SiteScore Report (評分 + Evidence) → ApprovalPanel (送審/核准 GO/WAIT/REJECT)
→ Decision Audit (留痕)
```

- 每一跳保留來源情境（從 SiteScore 開核准抽屜，關閉後回到原列表狀態）。
- 核准為高風險：完整 ApprovalPanel、必填 reason、二次確認、後端 Audit，回傳 `decision_id`。

### 8.2 營運干預流（operations）

```text
Alerts (四燈橙/紅) → Root Cause Evidence → InterventionTimeline (建單→eligibility→conflict
→approve→execute→observe→outcome→close)
```

- 干預核准與執行分離；觀察窗未成熟不得宣稱效果（domain 元件規則）。

### 8.3 通用「從通知/任務切入」流

```text
Bell/Notification 或 Tasks badge → Notification/Task Center → 列表選列開 Drawer
→「在工作區開啟」進入完整 Detail（切到對應 workspace，URL 反映）→ 處理/核准 → 回授計數
```

- 跨 workspace 切入時，URL 一次到位（workspace + module + entity + drawer），可分享。
- 處理完成後計數回授（非 optimistic），Toast 輕回饋 + 必要時 inline 狀態更新。

---

## 9. Keyboard & Accessibility Expectations（normative）

承 visual system §9：

- **全域快捷**：`Cmd/Ctrl+K` 命令面板；`/` 聚焦搜尋；`g h` 去 Home、`g t` 去 Tasks、`g n` 去 Notifications（建議；可由 user pref 關閉）；`Esc` 關 overlay/drawer。
- **焦點可見**：所有互動元件焦點可見（`color.border.focus`）；焦點順序 Header → Sidebar → Main → Drawer。
- **skip-to-content**：跳過 chrome 直達 `main`。
- **landmark / aria**：`banner`/`navigation`/`main`/`complementary`；當頁 `aria-current="page"`；breadcrumb 為 `nav` + ordered list；排序 `aria-sort`。
- **Drawer/Modal**：focus trap + Esc + 開啟移入/關閉還原焦點。
- **替代呈現**：地圖有列表替代、圖表有資料表替代；風險狀態顏色 + 文字 + icon/pattern + tooltip。
- **對比度**：正文 WCAG AA（4.5:1），大字/UI 元件 3:1；`high-contrast` theme 進一步提升。
- **reduced motion**：尊重 `prefers-reduced-motion`；critical alert 不閃爍。

---

## 10. Copy & Information Density in Navigation

承 visual system §8.4（三層語言）：

- 導覽標籤用**業務語言**名詞短語（`Listing 收件匣`、`今日營運`），不用技術代碼當選單名。
- 狀態碼（JobStatus / DecisionStatus / DataStatus / ModelStatus）與模型/版本/ID 保留英文原碼。
- 密度：列表/收件匣類頁面（Task Center、Notification Center、Listing Inbox）預設 `compact`；決策/卡片頁預設 `comfortable`；`2xl` wall screen 套 `presentation`。密度只改間距/字級，不改語意。
- 計數/SLA 文案明確（`12 筆待處理 · 3 筆逾 SLA`），不只一個漂亮數字；逾期/風險同時有文字 + 顏色 + icon。

---

## 11. Implementation Directives（給前端 worker 的硬性指令）

1. **路由即狀態**：用 §6 的 URL 契約；任何可分享狀態進 URL，重新整理可還原。
2. **權限過濾在導覽層**：workspace / sidebar item / command / 搜尋結果一律先過權限；無權限不渲染，deep link 受限 route → 403。
3. **三全域捷徑常駐**：Search / Tasks / Notifications 在所有頁面從 Header 直達；計數非 optimistic。
4. **切入流程保留情境**：從通知/任務/搜尋切入 entity 時 URL 一次到位（workspace+module+entity+drawer），返回回到來源列表狀態。
5. **高風險動作走完整流程**：Admin、核准、override、export 不得在列表/抽屜 optimistic；必走二次確認 + 理由 + 後端 Audit。
6. **只用既有元件契約與 token**：Sidebar / CommandPalette / Drawer / Table / ApprovalPanel 等以 `ODAY_PLUS_COMPONENT_CONTRACTS.md` 為準。
7. **a11y 與鍵盤**：實作 §9 全部條款；命令面板與全域快捷可用、focus 管理正確。
8. **不發明導覽語意**：不自創 workspace key、route 結構、狀態碼或把模組頁面內容塞進殼層。

---

## 12. Handoff Checklist（導覽與流程）

- [ ] 三層 IA（Global / Workspace / Module / Entity）落實；Level 0 由殼層擁有，Level 2+ 為插槽。
- [ ] Workspace switcher 權限過濾、切換導向 workspace Home、保留全域偏好、不跨域帶 filter。
- [ ] Sidebar 依 workspace 渲染、當頁高亮、全域捷徑常駐底部、權限過濾、唯讀標記。
- [ ] URL 契約：path（workspace/module/entity）+ query（tab/filter/sort/page/q/dateRange/選中/drawer）可還原與分享；偏好不進 URL。
- [ ] Breadcrumb：Detail 頁必備、與 URL 一致、mobile 收為返回。
- [ ] Global Search / Command Palette：跨工作區、權限過濾、`Cmd/Ctrl+K`、不列無權限動作、結果可 deep link。
- [ ] Task Center / Notification Center：列表 + tab + drawer + 計數回授 Header；高風險核准不 optimistic；critical alert 不閃爍。
- [ ] Admin Shell：role-gated、route 結構就緒、無權限不顯入口/deep link→403、管理動作走 Audit。
- [ ] 跨工作區流程（展店/干預/從通知切入）每步可 deep link、保留來源情境、prediction↔decision↔outcome 分離。
- [ ] 鍵盤與 a11y：全域快捷、skip-to-content、landmark、focus 管理、替代呈現、對比度、reduced motion。

---

## 13. 驗收條件

本文件作為 R0 Design Gate 交付物，需滿足：

- 定義三層 IA、workspace 模型與切換、Sidebar 導覽結構（含 R0 workspace 清單與範例 map）。
- 定義 Global Search、Command Palette、Task Center、Notification Center 四個全域表面的用途、行為與權限規則。
- 給出完整的 URL / 路由狀態契約（path + query + 可還原/可分享 + breadcrumb 模型）。
- 定義 Admin（管理）殼層的 sections、route 與權限門檻。
- 描述跨工作區工作流程，且每步可 deep link、保留情境、決策層分離。
- 涵蓋鍵盤、a11y、copy hierarchy 與資訊密度的導覽層期望。
- 提供硬性實作指令與可逐條檢查的 handoff checklist。
- 與殼層 blueprint、R0 screen inventory 及 UXD-001 三份文件互相引用且不矛盾。
- 可被 `ODP-R0-004` 直接據以實作導覽與路由，無需自行發明 UX。
