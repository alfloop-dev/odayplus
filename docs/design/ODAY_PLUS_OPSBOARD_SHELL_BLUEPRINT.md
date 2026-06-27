---
doc_id: ODP-R0-OPSBOARD-SHELL-BLUEPRINT
title: "ODay Plus OpsBoard Shell Blueprint"
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
  - docs/design/ODAY_PLUS_DESIGN_TOKENS.md
  - docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md
  - docs/design/ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md
  - docs/design/ODAY_PLUS_R0_SCREEN_INVENTORY.md
---

# ODay Plus OpsBoard Shell Blueprint

## 1. Purpose & How to Read

本文件把 ODay Plus 的 **OpsBoard 應用殼層（app shell）** 與 **首頁（Home / Today）** 收斂成一份可直接交給 LLM 前端 worker 實作的藍圖。它回答：

1. OpsBoard 殼層由哪些固定區域組成？每個區域的職責、內容、互動邊界是什麼？
2. 殼層在 desktop / tablet / mobile 三個尺寸如何重排？哪些能力在 mobile 被刻意拿掉？
3. 首頁（每個角色登入後的第一個畫面）長什麼樣、放什麼、空/載入/錯誤狀態如何處理？
4. 殼層如何承載 loading / error / degraded / offline / 權限不足等系統狀態？

本文件是**殼層與首屏的版面與行為契約**。它不重新定義：

- Token 具體值 → 見 `ODAY_PLUS_DESIGN_TOKENS.md`（唯一值來源）。
- 元件 props / states / a11y → 見 `ODAY_PLUS_COMPONENT_CONTRACTS.md`（唯一契約來源）。
- 視覺方向、密度、狀態語言、實作硬性指令 → 見 `ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md`。
- 路由、IA、工作區切換、搜尋、通知、任務中心、管理殼層的導覽與流程 → 見 `ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md`。
- 每條 R0 route 的逐頁規格 → 見 `ODAY_PLUS_R0_SCREEN_INVENTORY.md`。

衝突解析：**值看 token 文件、契約看 component 文件、規則看 visual system、本文件負責殼層與首屏的版面與狀態**。四者不一致時須於同一 PR 內一起修正。

### 1.1 Normative 語彙

「**必須 / 不得 / 一律**」為硬性條款，違反者 PR 應退回。「**建議 / 預設**」為預設值，worker 可在記錄理由後調整。

---

## 2. Shell Scope（R0 殼層邊界）

R0 OpsBoard 殼層**只擁有 chrome 與跨工作區的共用表面**：

- 固定骨架：Global Header、Sidebar、Page Header、Filter/Toolbar Bar、Content Area、可選 Right Drawer、overlay 層（Toast / Modal / Command Palette）。
- 跨工作區共用畫面：Home / Today、Task Center、Global Search、Notification Center、Settings、Admin Shell、系統狀態頁（auth / 403 / 404 / 500 / offline / maintenance）。

殼層**不擁有**任何模組業務頁面內容（HeatZone、SiteScore、ForecastOps … 的工作頁）。模組頁面由各自的 UX 規格與實作 worker 提供，並**插槽進入殼層的 Content Area**。殼層只保證：給定一個 workspace 與一個 route，能正確渲染 header / sidebar / page header / content slot / drawer slot，並處理 loading / error / empty / permission 四態。

> 對應 `ODAY_PLUS_COMPONENT_CONTRACTS.md §3`（AppShell / GlobalHeader / Sidebar / Toolbar / PageHeader / Drawer）。本文件描述「這些元件如何組成一個畫面」，元件本身的 props/states 不在此重複。

---

## 3. App Shell Anatomy（固定架構，不得改）

承 `ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md §4.1`，殼層的固定骨架：

```text
┌───────────────────────────────────────────────────────────────────────┐
│ R0  Global Header (sticky, layout.header-height)                        │
│  [logo] [workspace switcher] ──── [global search] ──── [tasks][bell]    │
│                                              [env badge] [user menu]     │
├──────────┬────────────────────────────────────────────────┬────────────┤
│ R1       │ R2  Page Header                                 │ R4         │
│ Sidebar  │  title · summary · status · primary/secondary   │ Right      │
│ (work-   │  actions · breadcrumb · last updated            │ Drawer /   │
│  space   ├────────────────────────────────────────────────┤ Detail     │
│  nav)    │ R3a Filter / Toolbar Bar (optional)             │ Panel      │
│          ├────────────────────────────────────────────────┤ (optional, │
│          │ R3b Content Area                                │  overlays  │
│          │  (table / map / chart / cards / form)           │  R3b on    │
│          │                                                 │  md/sm)    │
└──────────┴────────────────────────────────────────────────┴────────────┘
  R5 Overlays: Toast · Modal · Command Palette (Cmd/Ctrl+K)   z 見 token §8
```

### 3.1 Region 職責表（normative）

| Region | 名稱 | 職責 | 元件 | 不得 |
|---|---|---|---|---|
| R0 | Global Header | 跨工作區的全域控制：身分、環境、工作區切換、全域搜尋、任務、通知、使用者選單 | `GlobalHeader` | 不放單一工作區才有意義的頁面動作 |
| R1 | Sidebar | 當前 workspace 的導覽（第一層任務、第二層模組頁），當頁高亮，權限過濾 | `Sidebar` | 無權限項目**不渲染**（非 disabled） |
| R2 | Page Header | 當前頁的標題、一句話摘要、狀態、主/次動作、breadcrumb、last updated | `PageHeader` | Detail 頁不可省略 breadcrumb 與 deep link |
| R3a | Filter / Toolbar Bar | 列表/地圖頁的篩選、saved view、欄位可見性、批次動作、export、density 切換 | `Toolbar`/`FilterBar` | 高敏感 export 不可省二次確認＋理由＋Audit |
| R3b | Content Area | 頁面主體（資料是主角）；模組頁面插槽於此 | 視頁面而定 | 不在此注入殼層 chrome 邏輯 |
| R4 | Right Drawer | 列表中的快速查看與次要操作，可上一筆/下一筆、deep link、保留列表狀態 | `Drawer` | 不放需完整審查的大型模型報告（用 Page） |
| R5 | Overlays | Toast（輕回饋）、Modal（確認/短表單/危險操作）、Command Palette | `Toast`/`Modal`/`CommandPalette` | Modal 不裝大型報告或複雜表格 |

### 3.2 Region 持久性與狀態歸屬

| 狀態 | 歸屬 | 持久層 |
|---|---|---|
| Sidebar collapsed / expanded | local UI state | localStorage（per device），不進 URL |
| Active workspace | URL（route segment）+ session | URL 為準，見 navigation spec §3 |
| Active route / tab / filter / sort / page / date range | URL query/segment | URL 為準（`ODP-UX-05 §7.3`） |
| Drawer open + 選中 entity | URL（deep-linkable）| URL 為準，可分享 |
| Density 偏好 | user preference + page default | 後端 user pref，fallback localStorage |
| Theme（light/dark/high-contrast/presentation） | user preference | 後端 user pref，fallback system |

規則（normative）：

- Sidebar 收合**不得**觸發 Content Area 內地圖/圖表的重繪（`ODP-UX-05 §10.3`）。
- 任何可被分享或書籤的狀態（route / tab / filter / 選中項）一律進 URL，不只存元件內 state。
- 重新整理頁面後，URL 能還原到同一畫面狀態（含 drawer 開啟與選中項）。

### 3.3 Landmark 與焦點順序（a11y，normative）

- Landmark：R0=`banner`、R1=`navigation`、R3=`main`、R4=`complementary`。
- 提供 **skip-to-content**（跳過 header/sidebar 直達 `main`）。
- 焦點預設順序：Header → Sidebar → Main → Drawer。Drawer/Modal 開啟時 focus trap，關閉時還原焦點到觸發元素。
- 對應 `ODAY_PLUS_COMPONENT_CONTRACTS.md §3.1`（AppShell A11y）。

---

## 4. Global Header（R0）細部

### 4.1 內容與排列（左 → 右）

```text
[ ☰ ][ logo ][ Workspace ▾ ]  ……  [ 🔍 Search (Cmd/Ctrl+K) ]  ……  [ ✔ Tasks ⁸ ][ 🔔 Bell ³ ][ env: STAGING ][ 👤 User ▾ ]
```

- **☰** 僅 mobile/tablet 顯示，切換 Sidebar 抽屜。
- **Logo**：點擊回到當前 workspace home（非整站 reset）。
- **Workspace Switcher**：見 navigation spec §3；顯示當前工作區，下拉切換，無權限工作區不列。
- **Global Search**：可見輸入框（desktop）或 icon（mobile）；`Cmd/Ctrl+K` 開 Command Palette。
- **Tasks**：待辦數 badge（指派給我、待我核准、我發起待回）。
- **Bell**：新通知數 badge（警示 / 核准請求 / Job 結果）。
- **Env Badge**：`dev` / `staging` / `production`，**必須有文字**，不得只靠顏色（`ODAY_PLUS_COMPONENT_CONTRACTS.md §3.2`）。production 以外環境視覺上更醒目，提醒「非正式」。
- **User Menu**：身分、角色、切換 theme/density、settings、登出。

### 4.2 Badge 計數規則

- Tasks / Bell 計數來自後端，未讀/待辦 > 99 顯示 `99+`。
- 計數為 0 時不顯示數字 badge（icon 保留）。
- 計數**不得 optimistic**：核准/讀取後以後端回應為準再更新（高風險動作見 visual system §10.3）。

### 4.3 Header 狀態

| 狀態 | 行為 |
|---|---|
| loading（首次） | logo + skeleton 佔位 workspace/search/badges；不阻擋 skip-to-content |
| degraded | 若通知/任務服務不可用，badge 顯示 `—` 並 tooltip 說明，不顯示假計數 |
| offline | env badge 旁顯示 `OFFLINE` chip；見 §9.4 |

---

## 5. Sidebar（R1）細部

### 5.1 結構

兩層導覽：第一層為當前 workspace 的主要任務群組，第二層為模組頁面。當頁高亮（`aria-current="page"`）。詳細的工作區→導覽項對應見 `ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md §4`。

```text
WORKSPACE: 展店 (Expansion)
  ▸ Today / Home
  ▸ HeatZone Radar           (model: purple dot if model-driven)
  ▸ Listing Inbox            ⁵      ← 待處理數
  ▸ Candidate Sites
  ▸ SiteScore Reports        🔒RO   ← 唯讀標記（可讀不可寫）
  ──────────
  ▸ Tasks
  ▸ Notifications
```

### 5.2 規則（normative）

- 無權限項目**不渲染**（不是 disabled），可讀不可寫項目顯示唯讀標記（`🔒RO` / `readOnly`），且其頁面不顯示操作按鈕（`ODAY_PLUS_COMPONENT_CONTRACTS.md §3.3`）。
- Collapsed 狀態只剩 icon + tooltip label；展開寬度 `layout.sidebar-width`、收合寬度 `layout.sidebar-collapsed`（值見 token 文件）。
- Sidebar 內可帶 inline 計數 badge（如 Listing Inbox 待處理數），規則同 §4.2。
- 鍵盤可達；上下鍵在項目間移動，Enter 進入。

---

## 6. Page Header（R2）與 Filter Bar（R3a）

### 6.1 Page Header 必含

Title（`h1`）、Subtitle/Summary（一句話重點）、Status Badge、Primary Action、Secondary Actions、Breadcrumb（`nav` + ordered list）、Last Updated。Detail 頁**必須**有 breadcrumb 與 deep link（`ODAY_PLUS_COMPONENT_CONTRACTS.md §3.5`）。

Copy hierarchy（承 visual system §8.4 三層語言）：

- Title：名詞短語，業務語言（例：`Listing 收件匣`、`今日營運`）。
- Summary：一句話現況，可帶關鍵數字（例：`12 筆待處理 · 3 筆逾 SLA`）。
- Status：用 §canonical 狀態碼 badge（保留英文碼），不自創。

### 6.2 Filter / Toolbar Bar

僅列表/地圖/可批次頁出現。filters、saved views、column visibility、batch actions、export、density 切換。filter/sort/page/selected tab/date range **與 URL 同步**（`ODP-UX-05 §7.3`）。批次列宣告選取數（`region` + screen reader）。

---

## 7. Home / Today（首屏）藍圖

「首頁」是每個角色登入後的第一個畫面，也是本任務 acceptance 中要求覆蓋 desktop/mobile responsive 的「first screen」。它**不是**行銷 hero，而是**角色化的營運待辦與現況控制台**（decision-first，承 visual system §3）。

### 7.1 Route 與角色化

- Route：`/`（登入後）→ 依使用者預設 workspace 與角色解析；亦可 deep link `/home`。
- Home 內容**角色化**：展店審查、營運主管、定價、財務法務、AI/資料、加盟主、稽核各看到對應卡片集合。無權限的區塊不渲染。
- Home 不發明新狀態語言；所有狀態 badge/chip 走 canonical 狀態碼與 status token。

### 7.2 版面（desktop `lg`+）

```text
R2 Page Header
   Title: 今日營運 (Today)        Summary: 你有 8 項待辦 · 2 項逾 SLA · 資料 FRESH
   Status: [env] [data freshness] Last updated: 09:12
R3b Content Area  (12-col grid)
 ┌───────────────────────────┬───────────────────────────┐
 │ A. 待我處理 (My Queue)     │ B. 待我核准 (Pending      │
 │    Task cards / list       │    Approval) ApprovalPanel │
 │    priority + SLA + owner   │    summaries (high-risk)   │
 ├───────────────────────────┼───────────────────────────┤
 │ C. 重點警示 (Alerts)       │ D. 模型/資料健康          │
 │    FourLightBadge + AlertCh │    DataStatusBadge /       │
 │    ip → 來源 detail         │    ModelVersionBadge 摘要  │
 ├───────────────────────────┴───────────────────────────┤
 │ E. 我的工作區捷徑 (Workspace shortcuts)                │
 │    每個有權限的 workspace 一張卡：名稱 + 關鍵計數      │
 └────────────────────────────────────────────────────────┘
```

區塊語意（normative）：

- A/B 是**決策入口**（decision-first）：直接帶人去處理或核准，不是純資訊瀏覽。
- B（待我核准）內任何高風險項目**不得在 Home 直接 optimistic 核准**；點擊進入完整 ApprovalPanel 頁/抽屜（visual system §10.3、`ODAY_PLUS_COMPONENT_CONTRACTS.md §4.15`）。
- C 警示一律「顏色 + 文字 + icon/pattern」，可鍵盤聚焦並導向來源 detail。
- D 用 `DataStatusBadge` / `ModelVersionBadge` 呈現新鮮度與模型階段，不把預測呈現為「已決定」。
- 各區塊卡片數量有上限，超量以「查看全部 →」導向對應列表頁（Task Center / Notification Center / workspace 頁），不在 Home 無限堆疊。

### 7.3 Home 四態

| 態 | 呈現 |
|---|---|
| loading | 每區塊 skeleton（卡片骨架）；Page Header summary 顯示 skeleton，不顯示假數字 |
| empty | 各區塊走 `EmptyState`（title + description + nextActions + docLink），例如「目前沒有待辦」附「瀏覽工作區 →」。不得只顯示「沒有資料」（`ODAY_PLUS_COMPONENT_CONTRACTS.md §4.12`） |
| error | 區塊級錯誤隔離：A 失敗不拖垮 B/C/D；錯誤卡顯示摘要 + 錯誤代碼 + 可重試 + correlation_id + 時間（§9.3）。degraded 時保留可用區塊 |
| partial / stale | 區塊上方顯示 `STALE`/`PARTIAL` chip，內容仍顯示但標示資料限制 |

### 7.4 角色化內容矩陣（範例，實作以後端權限與 workspace 配置為準）

| 角色 | A 我的佇列 | B 待我核准 | C 警示 | D 健康 | E 捷徑 |
|---|---|---|---|---|---|
| 展店審查 | Listing 待解析/待評分 | SiteScore 送審 | 商圈飽和警示 | 地理編碼/資料新鮮度 | 展店工作區 |
| 營運主管 | 干預待建單 | 干預/調價核准 | 四燈橙/紅門市 | ForecastOps 模型階段 | 營運/定價工作區 |
| 定價 | 待提調價方案 | 調價核准 | 硬限制違反 | PriceOps 模型 | 定價工作區 |
| 財務法務 | 估值待審 | 估值/底價核准 | 合規警示 | DataRoom 完整度 | AVM 工作區 |
| 加盟主 | 待確認任務 | —（通常無核准權） | 我的門市警示 | 我的門市資料 | 我的門市 |
| 稽核 | 待匯出 Evidence | — | 高風險決策待稽核 | Audit 完整度 | 稽核工作區 |

---

## 8. Responsive Behavior（desktop / tablet / mobile）

斷點 `sm/md/lg/xl/2xl`，值見 token 文件 §10。承 visual system §5.3：**desktop-first；mobile 不承擔完整模型審查**。

### 8.1 殼層重排

| Region | `lg`+ (desktop) | `md` (tablet) | `sm` (mobile) |
|---|---|---|---|
| Global Header | 完整：search 框 + 全部 icon | 完整，search 收為 icon | logo + ☰ + search icon + bell；workspace 進抽屜 |
| Sidebar | 固定欄，可收合 | 預設收合為 icon-rail，可展開 | 隱藏，由 ☰ 開全幅抽屜（overlay），選後關閉 |
| Page Header | title + summary + 全部動作 | 同，secondary actions 收 overflow `⋯` | title + 1 primary action；其餘進 `⋯`；breadcrumb 收為「‹ 返回」 |
| Filter Bar | 完整 inline | filters 收為「篩選」按鈕 → drawer/sheet | 同 md；批次動作走底部 action sheet |
| Content | 多欄/表格/地圖完整 | 表格可橫向捲動；地圖簡化 | 卡片化單欄；表格降級為卡片列表 |
| Right Drawer | 並排右側 | overlay 覆於 content | 全幅 sheet（bottom/right），Esc/手勢關 |
| Overlays | 同 | 同 | Modal 全幅；Command Palette 全幅 |

### 8.2 能力分級（normative，承 visual system §5.3）

- **`lg`+**：完整模型審查、地圖、solver 結果、複雜表格、export。**只在此保證**。
- **`md`**：可檢視關鍵摘要與任務、可做核准（ApprovalPanel 可用），但完整模型審查仍導向桌機體驗或標示「建議於桌機檢視」。
- **`sm`**：只支援任務確認、通知、簡易回報、加盟主查看門市摘要。複雜審查頁顯示「此頁建議於較大螢幕檢視」並提供可在 mobile 完成的子集（如：只看摘要 + 確認）。
- **`2xl`（command center / wall screen）**：預設套 `presentation` 密度（放大字級與間距）。

### 8.3 Home 的 responsive

- `lg`+：§7.2 的雙欄四象限 + 捷徑列。
- `md`：象限改為兩欄堆疊（A、B、C、D 依序），捷徑列水平捲動。
- `sm`：單欄垂直；順序固定為 A 我的佇列 → B 待我核准 → C 警示 → D 健康 → E 捷徑；每區塊「查看全部 →」導向對應列表。核准在 mobile 可進行但仍走完整 ApprovalPanel（不簡化必填 reason 與二次確認）。

---

## 9. System States（殼層層級）

殼層必須統一承載以下狀態，模組頁面不得各自發明。

### 9.1 Loading

- API → skeleton；大型報告 → Job progress；Solver → queue/running/elapsed；地圖 → layer loading。
- **大型 Job 不顯示假進度百分比**（NetPlan solver、訓練、scoring），只顯示階段狀態（visual system §7.2、`ODAY_PLUS_COMPONENT_CONTRACTS.md §6.1`）。
- 殼層 chrome（header/sidebar）優先渲染，content 區獨立 loading，不整頁白屏。

### 9.2 Empty

一律 `EmptyState`：`title` + `description` + `nextActions[]` + 可選 `docLink`。禁止只顯示「沒有資料」。

### 9.3 Error

- 顯示：錯誤摘要 / 錯誤代碼 / 是否可重試 / 建議下一步 / `correlation_id` / 發生時間。
- **禁止只顯示 `Something went wrong`**（visual system §8.4）。
- 區塊級隔離：地圖失敗但列表可用、圖表失敗但表格可用、模型報告失敗但歷史可讀（degraded mode）。
- 重大錯誤不可只用 Toast，必須 inline error 或 error page（`ODAY_PLUS_COMPONENT_CONTRACTS.md §4.9`）。

### 9.4 Degraded / Offline

- `AppShell` 支援 `degraded` 模式：部分服務不可用時，受影響 region 顯示降級提示，其餘照常。
- Offline：header 顯示 `OFFLINE` chip；可寫動作（核准/override/export/調價/發布）一律**禁用並 tooltip 說明**，不得 optimistic 排隊高風險動作。唯讀內容可從 cache 呈現並標示「離線快取，時間 …」。

### 9.5 Permission（權限不足）

- 無權限項目於 Sidebar/Command Palette **不渲染**；直接 deep link 到無權限 route 時導向 `403` 頁（見 R0 screen inventory），說明缺少的權限與申請路徑，不洩漏受限內容。
- 可讀不可寫：顯示唯讀摘要，不顯示操作按鈕（visual system §10.4）。

---

## 10. Environment & Theming in the Shell

- 環境：`dev` / `staging` / `production`，由 `AppShell.environment` 傳入；env badge 必帶文字，非 production 視覺更醒目。
- Theme：`light` / `dark` / `high-contrast` / `presentation`；殼層只讀 token，不感知 theme（visual system §8.1）。User Menu 可切換 theme 與 density。
- `presentation` 密度於 `2xl` 預設套用；切換 density 只改間距與字級 scale，不改語意色與資訊層級。

---

## 11. Implementation Directives（給前端 worker 的硬性指令）

違反任一條，PR 應退回。

1. **殼層只用既有元件契約**：`AppShell` / `GlobalHeader` / `Sidebar` / `Toolbar` / `PageHeader` / `Drawer` / overlay 元件，缺欄位先擴充 `ODAY_PLUS_COMPONENT_CONTRACTS.md` 再實作（visual system §10.2）。
2. **一律走 semantic token**，不在殼層硬編色/字級/間距/圓角/陰影/z-index/動畫時間（visual system §10.1）。z-index 走 `z.*`（token §8），Command Palette 在最上層。
3. **狀態與權限**：無權限不渲染（非 disabled）；可寫高風險動作不得 optimistic，且須觸發後端 Audit（visual system §10.3）。
4. **URL 即狀態**：route / workspace / tab / filter / 選中項 / drawer 開啟一律可由 URL 還原與分享。
5. **四態必備**：每個 content slot 都要有 loading / empty / error / (stale/partial) 呈現，不得裸接 API。
6. **a11y baseline**：landmark、skip-to-content、focus trap、`aria-current`、鍵盤可達、對比度 WCAG AA、`prefers-reduced-motion` 尊重（visual system §9）。
7. **不發明**：不自創狀態名/狀態色/密度檔位/資訊層級順序；不把行銷 hero、顏色當唯一訊號、閃爍 critical alert 引入殼層。
8. **Testing hooks**：殼層元件提供 `data-testid`；殼層納入 Storybook + 視覺回歸（light/dark/high-contrast）；shell + Home 關鍵 flow 走 Playwright（`ODP-UX-05 §17`）。

---

## 12. Handoff Checklist（殼層與首屏）

實作 worker 在送審前自檢，reviewer 逐條檢查：

- [ ] R0–R5 六個 region 全部就位，職責不混用；Content/Drawer 為插槽，不含殼層 chrome 邏輯。
- [ ] Sidebar 收合不重繪地圖/圖表；收合狀態存 localStorage、不進 URL。
- [ ] Workspace / route / tab / filter / 選中項 / drawer 全部可由 URL 還原與分享；重新整理還原同畫面。
- [ ] Global Header：env badge 帶文字、非 production 醒目；Tasks/Bell 計數非 optimistic、0 不顯數字、>99 顯 `99+`。
- [ ] 無權限項目不渲染；可讀不可寫顯示唯讀標記且無操作按鈕；deep link 到無權限 route → 403 頁。
- [ ] Home 角色化、decision-first；A/B/C/D/E 區塊四態齊備；高風險核准不在 Home optimistic。
- [ ] Responsive：`lg`+ 完整、`md` 摘要+核准、`sm` 任務/通知/回報/加盟主查看；複雜審查頁在 sm 標示建議桌機。
- [ ] System states：loading（含大型 Job 無假百分比）、empty（EmptyState 帶 nextActions）、error（代碼+correlation_id+下一步）、degraded/offline、403。
- [ ] 全部走 semantic token、既有元件契約、canonical 狀態碼；a11y baseline 通過 axe + 鍵盤導覽。
- [ ] 殼層 + Home 有 Storybook、視覺回歸（light/dark/high-contrast）、Playwright smoke。

---

## 13. 驗收條件

本文件作為 R0 Design Gate 交付物，需滿足：

- 定義 OpsBoard 殼層的固定 region 架構、各 region 職責、狀態歸屬、a11y landmark 與焦點順序。
- 覆蓋 desktop / tablet / mobile 三尺寸的殼層重排與能力分級（含 mobile 刻意拿掉的能力）。
- 定義首頁（Home / Today）的角色化版面、區塊語意、四態與 responsive 行為。
- 統一殼層層級的 loading / empty / error / degraded / offline / permission 狀態。
- 提供 LLM worker 的硬性實作指令與可逐條檢查的 handoff checklist。
- 與 `ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md`、`ODAY_PLUS_DESIGN_TOKENS.md`、`ODAY_PLUS_COMPONENT_CONTRACTS.md`、`ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md`、`ODAY_PLUS_R0_SCREEN_INVENTORY.md` 互相引用且不矛盾。
- 可被 `ODP-R0-004`（OpsBoard shell 實作）直接據以實作，無需自行發明 UX。
