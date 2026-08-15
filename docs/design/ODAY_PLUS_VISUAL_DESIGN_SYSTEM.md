---
doc_id: ODP-R0-VISUAL-DESIGN-SYSTEM
title: "ODay Plus Visual Design System"
version: 0.1.0
status: draft
document_class: design-system
project: ODay Plus
language: zh-TW
updated_at: 2026-06-26
owner: "Product Design / Frontend"
approvers: "Product Lead / Frontend Lead"
content_format: markdown
source_documents:
  - ODP-UX-01_INFORMATION_ARCHITECTURE_AND_NAVIGATION.md
  - ODP-UX-02_DESIGN_SYSTEM.md
  - ODP-UX-04_MAP_AND_DATA_VISUALIZATION_SPECIFICATION.md
  - ODP-UX-05_FRONTEND_TECHNICAL_DESIGN.md
related_documents:
  - docs/design/ODAY_PLUS_DESIGN_TOKENS.md
  - docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md
---

# ODay Plus Visual Design System

## 1. Purpose

本文件把分散在第七批 UX 文件（`ODP-UX-01` ～ `ODP-UX-05`）裡的設計語言，收斂成一份**可執行、可給 LLM worker 直接實作**的視覺方向基線。它回答三個問題：

1. ODay Plus 是什麼產品個性？前端應該長什麼樣、給人什麼感覺？
2. 哪些視覺決策已經被定死，工程 worker **不得自行發明**？
3. 工程 worker 在實作頁面時，應該套用哪一層的 token、密度、節奏與元件規格？

Source of truth 分層：

| Layer | Source of truth | 本文件角色 |
|---|---|---|
| 正式 UX 規格 | `ODP-UX-01` ～ `ODP-UX-05` | 不取代，只收斂與補上具體值 |
| Token 具體值 | 本系列三份 design 文件 | `ODAY_PLUS_DESIGN_TOKENS.md` 為唯一值來源 |
| 元件契約 | 本系列三份 design 文件 | `ODAY_PLUS_COMPONENT_CONTRACTS.md` 為唯一契約來源 |

本文件是**視覺方向與規則**；token 具體值在 `ODAY_PLUS_DESIGN_TOKENS.md`；元件 props / states / a11y 在 `ODAY_PLUS_COMPONENT_CONTRACTS.md`。三份文件互為引用，不得彼此矛盾。

---

## 2. Product Personality

ODay Plus OpsBoard 是一個**營運決策平台**，不是行銷官網、不是消費級 App、也不是純 BI dashboard。它的使用者是要為高風險商業決策負責的人：展店審查、營運主管、定價團隊、財務法務、AI/資料團隊、加盟主、稽核。

### 2.1 一句話定位

> 一個讓人能在「模型說了什麼、為什麼這樣說、我能做什麼、做完留下什麼紀錄」之間快速移動，並對每個決策負責的營運控制台。

### 2.2 個性關鍵字（normative）

| 是 | 不是 |
|---|---|
| Calm / 沉穩、低飽和、不搶眼 | Flashy / 高飽和、漸層、霓虹 |
| Dense-but-legible / 高資訊密度但可判讀 | Sparse marketing hero / 大留白行銷風 |
| Evidence-forward / 證據與不確定性優先 | Single-number confidence / 只給一個漂亮數字 |
| Accountable / 每個動作可追溯 | Magical / 「AI 幫你決定好了」 |
| Operational / 工具感、鍵盤友善 | Playful / 卡通化、過度動畫 |

### 2.3 視覺語氣

- **中性底、語意色點綴。** 介面主體是中性灰階；顏色被嚴格保留給「狀態、風險、模型、地圖」這四種語意。色彩是訊號，不是裝飾。
- **資料是主角。** 表格、地圖、圖表、卡片是主畫面；chrome（header / sidebar / toolbar）退到背景。
- **不確定性可見。** 任何預測、估值、效果估計都以區間或信心呈現，視覺上不得讓單點值看起來像已成定局。
- **可追溯的克制。** 高風險動作（核准、override、調價、發布、rollback）在視覺上要明顯、要二次確認、要留痕；其餘動作保持安靜。

### 2.4 反面清單（Do NOT，工程 worker 一律禁止）

- 不用全幅行銷 hero、不用大圖背景、不用品牌漸層當主視覺。
- 不用顏色作為唯一風險訊號（必須同時有文字 + icon/pattern）。
- 不把模型輸出呈現為「已決定」；預測、建議、人工決策、執行、結果必須在視覺上分離。
- 不在 Modal 裡塞大型報告或複雜表格。
- 不為了多塞欄位而犧牲判讀（Clarity over density）。
- 不發明新的狀態名稱／狀態色；一律使用 §6 與 token 文件定義的語意。
- 不在元件內硬編色碼、字級、間距；一律走 semantic token。

---

## 3. Design Principles（normative）

承接 `ODP-UX-02 §2`，定為實作驗收條款：

1. **Clarity over density.** 先讓使用者回答「現在狀態是什麼／為什麼這樣判斷／我可以做什麼／做完留下什麼」，再談塞資料。
2. **Decision-first.** 每個工作頁以決策任務為中心，不是以資料瀏覽為中心。主視覺是建議 + 證據 + 動作，不是全部欄位。
3. **Prediction is not decision.** UI 必須分離：模型預測 → 系統建議 → 人工核准 → 實際執行 → 結果回收。
4. **Uncertainty is visible.** 所有預測與估值顯示 P10/P50/P90、Confidence、Coverage、Sample size、Data freshness、Model version、Feature snapshot time（依適用）。
5. **Audit by design.** 高風險元件內建 Actor / Timestamp / Reason / Approval / Override / Model version / Policy version / Before-After。
6. **Color is a signal, not decoration.** 語意色只用於 status / risk / model / map，且不得作為唯一訊號。
7. **Accessible by default.** 鍵盤可達、焦點可見、色盲友善、地圖有列表替代、圖表有資料表替代。

每一條都對應 §10 的「實作驗收條款」，PR review 可逐條檢查。

---

## 4. Layout System

### 4.1 App Shell（固定架構，不得改）

```text
┌─────────────────────────────────────────────────────────────┐
│ Global Header  (logo · workspace switcher · search · tasks · │
│                 notifications · env badge · user menu)        │
├──────────┬──────────────────────────────────────────────────┤
│ Sidebar  │ Page Header (title · summary · status · actions · │
│ (work-   │              breadcrumb · last updated)           │
│  space   ├──────────────────────────────────────────────────┤
│  nav)    │ Filter / Toolbar Bar                              │
│          ├───────────────────────────────────┬──────────────┤
│          │ Content Area                      │ Right Drawer /│
│          │ (table / map / chart / cards)     │ Detail Panel  │
│          │                                   │ (optional)    │
└──────────┴───────────────────────────────────┴──────────────┘
  Overlays: Toast · Modal · Command Palette (Cmd/Ctrl+K)
```

規則：

- Header 高度固定、sticky。Sidebar 隨 workspace 改變，無權限項目不顯示，可讀不可寫項目顯示唯讀標記。
- Sidebar 可收合；收合狀態存在 local UI state，不影響地圖重繪（見 `ODP-UX-05 §10.3`）。
- Right Drawer 用於列表中的快速查看與次要操作，支援 Esc 關閉、deep link、上一筆/下一筆、保留列表狀態。
- Modal / Toast / Command Palette 是 overlay 層，走 z-index token（見 token 文件 §8）。

### 4.2 Page Header（每個工作頁必備）

必含：Title、Subtitle/Summary（一句話重點）、Status Badge、Primary Action、Secondary Actions、Breadcrumb、Last Updated。所有 Detail 頁必須有 Breadcrumb 與 Deep Link（URL pattern 見 `ODP-UX-01 §4.4`）。

### 4.3 Information Hierarchy（每個 Detail 頁統一七層）

承 `ODP-UX-01 §11`，所有 Detail 頁由上而下：

```text
1. Summary        一句話重點
2. Status         業務 / 模型 / 資料新鮮度 / 核准 / Job
3. Evidence       正向因子 / 負向因子 / comparable / trend / confidence / 限制
4. Recommendation 標示 generated-by-system + policy/model version + requires approval
5. Decision       分離 prediction / recommendation / human decision / execution / outcome
6. Execution/Result
7. Version / Audit feature snapshot time / model / policy / actor / reason / outcome time
```

工程 worker 實作任何 Detail 頁時，**這七層的順序與語意是固定的**，可省略不適用層，但不得重排或混用。

### 4.4 Grid 與量測

- 基準間距單位 4px（`space.1`）。所有 padding / margin / gap 走 spacing token，不得用任意 px。
- Content max-width：一般工作頁不設硬上限（充分利用寬螢幕做表格/地圖）；純閱讀型報告區塊套用 `layout.readable-max`（見 token 文件）。
- 兩欄式（Content + Drawer）時 Drawer 寬度走 `layout.drawer-width` token。

---

## 5. Density & Rhythm

### 5.1 三檔密度（normative）

承 `ODP-UX-02 §4.3`，全站支援三檔密度，由使用者偏好 + 頁面預設決定：

| Density | 行高/列高 | 使用場景 | 預設於 |
|---|---|---|---|
| `comfortable` | 較鬆 | 一般使用者、決策頁、卡片頁 | 多數工作頁 |
| `compact` | 較緊、適合掃描 | 表格密集操作（Listing Inbox、Task List、Audit Log） | 列表/收件匣頁 |
| `presentation` | 放大字級與間距 | 管理層檢視、會議投影、wall screen | Executive / 簡報模式 |

密度只改間距與字級 scale，**不改語意色、不改資訊層級**。density 對應的具體尺寸見 token 文件 §9。

### 5.2 Vertical Rhythm

- Section 間距 `space.8`；卡片內區塊間距 `space.4`；表格列內間距由 density 決定。
- 同一頁不混用兩種卡片陰影層級；elevation 只用來表達「浮起/聚焦」，不用來裝飾。

### 5.3 Responsive

斷點（`sm/md/lg/xl/2xl`，值見 token 文件 §10）：

- **Desktop first.** 完整模型審查、地圖、solver 結果只保證 `lg` 以上。
- **Tablet（md）** 可檢視關鍵摘要與任務，可做核准。
- **Mobile（sm）** 僅支援任務確認、通知、簡易回報與加盟主查看，**不承擔完整模型審查**。
- **2xl（command center / wall screen）** 預設套 `presentation` 密度。

---

## 6. Status, Risk, Confidence & Model Language

這是 ODay Plus 視覺系統最關鍵的一節：**全站狀態語言統一，模組不得自創名稱或自選顏色。**

### 6.1 Status colour semantics（綁 token）

| 狀態 | 語意 | 典型場景 | Token |
|---|---|---|---|
| Green | 正常、達標、低風險 | 四燈綠、資料新鮮、Job 成功 | `color.status.green` |
| Yellow | 輕微偏離、需注意 | 四燈黃、資料快過期 | `color.status.yellow` |
| Orange | 高風險、需處置 | 四燈橙、干預建議 | `color.status.orange` |
| Red | 危急、阻擋、重大失敗 | 四燈紅、資料 QA fail、硬限制違反 | `color.status.red` |
| Gray | 未啟用、無資料、草稿 | 無樣本、未發布 | `color.status.gray` |
| Blue | 資訊、進行中 | Job Running、Canary | `color.status.blue` |
| Purple | 模型 / AI 相關 | Model version、Feature registry | `color.status.purple` |

### 6.2 不得作為唯一訊號（hard rule）

四燈與所有風險狀態必須同時提供：**顏色 + 文字 + icon/pattern + tooltip**。色盲模式以 icon/pattern 區分。Badge 一律帶文字（例如 `ORANGE`），不可只靠顏色。

### 6.3 Canonical state vocabularies（前端統一字彙，不得改字）

工程 worker 直接使用這些英文狀態碼（繁中介面保留英文狀態碼，見 `ODP-UX-01 §12` 與 §9 Localization）：

```text
Job:      QUEUED RUNNING SUCCEEDED FAILED CANCELLED PARTIAL RETRYING EXPIRED
Decision: DRAFT SYSTEM_RECOMMENDED PENDING_REVIEW APPROVED REJECTED OVERRIDDEN
          EXECUTED OBSERVING OUTCOME_READY CLOSED
Data:     FRESH STALE PARTIAL MISSING LOW_CONFIDENCE FAILED_QA BLOCKED
Model:    EXPERIMENTAL CANDIDATE CHALLENGER CHAMPION SHADOW CANARY PRODUCTION
          DEPRECATED ROLLED_BACK BLOCKED
```

### 6.4 Confidence 表達

Confidence **不以單一顏色代表**，必須附原因（sample size、資料新鮮度、comparable 稀疏、geocode 不確定等）。低信心時於圖表/卡片上方顯示 warning。

### 6.5 Model stage 顏色

模型階段色與一般 status 色分離，避免「模型階段」被誤讀成「業務風險」，但並非全部走同一色相；實際值以 `ODAY_PLUS_DESIGN_TOKENS §3.5` 為準（§11.2 決定論解析），分三組：

- **模型身分**走 purple 家族：`color.model.production`（purple.700）、`color.model.candidate`（purple.500）。
- **漸進評估 / 放量**走 blue（資訊、進行中，與 §6.1 的 Blue＝Canary 一致）：`color.model.shadow`（blue.500）、`color.model.canary`（blue.700）。
- **回滾**走 red：`color.model.rollback`（red.500）。此色相與 `color.status.red` 重疊但語意不同（模型回滾 vs 業務危急），依 §6.2 必須搭配文字／icon 區分，不得僅以紅色傳達。

---

## 7. Iconography & Motion

### 7.1 Icon 規則

- Icon 不能單獨傳達高風險狀態，必須搭配文字。
- 同一模組 icon 不得跨語意使用。
- 加盟主介面避免過度技術化 icon。
- Icon 集合語意分類見 `ODP-UX-02 §8`（Map/Store/Machine/Revenue/Forecast/Alert/Intervention/Price/Campaign/Valuation/Network/Model/Data/Audit/User/Lock/Warning/Rollback）。

### 7.2 Motion 規則

- Motion 用於引導注意，不用於裝飾。
- 地圖 layer 切換可淡入淡出；Drawer 開合要快。
- **Critical alert 不使用閃爍動畫**（避免干擾與可及性問題）。
- Loading 用 skeleton 或 stage indicator；大型 Job（如 NetPlan Solver）不顯示假進度百分比，只顯示階段狀態。
- Motion duration / easing 走 token（見 token 文件 §11）。`prefers-reduced-motion` 必須被尊重。

---

## 8. Theming & Localization

### 8.1 Themes

支援 `light` / `dark` / `high-contrast` / `presentation`。每個 theme 是一組 token 值覆寫（同名 semantic token，不同實際值），元件不感知 theme，只讀 token。

### 8.2 Map themes（獨立）

地圖另有獨立 theme：`light` / `dark` / `minimal` / `print_safe`，預設 `minimal` 以免干擾 HeatZone 與 Listing layer（見 `ODP-UX-04 §3.2`）。

### 8.3 Localization

- 系統預設語言：繁體中文。
- 保留英文原文：技術名詞、狀態碼、模型名稱、API/Event/Job ID。
  - 範例：`四燈狀態：ORANGE`、`模型版本：forecastops-growth-v1.2.3`、`資料狀態：STALE`。

### 8.4 Copy 規則（綁前端文案）

三層語言：Business（管理層/營運/加盟主，直接可執行）、Analytical（審查/行銷/財務，指標與區間）、Technical（AI/資料/稽核，版本與資料品質）。

- 禁用：`AI 決定`、`模型保證`、`一定會成長`、`絕對準確`、`立即改善`。
- 建議：`系統建議`、`預測區間`、`信心水準`、`需人工核准`、`觀察窗成熟後驗證`。
- Error copy 必含：發生什麼事 / 可能原因 / 下一步 / 錯誤代碼 / correlation_id。禁止只顯示 `Something went wrong`。

---

## 9. Accessibility（normative baseline）

- 鍵盤可操作；所有互動元件有可讀 label；焦點可見（`color.border.focus`）。
- 色彩不可作為唯一訊息；風險/四燈附 icon + pattern + text + tooltip。
- 圖表需有資料表替代；地圖 layer 需有列表替代。
- Modal / Drawer 焦點管理（focus trap + Esc）；Form error 關聯欄位；Toast 可被 screen reader 讀取。
- 高密度表格：sticky header、column resize、keyboard navigation、row focus、screen reader summary。
- 對比度：正文文字與背景至少 WCAG AA（4.5:1），大字與 UI 元件至少 3:1；`high-contrast` theme 進一步提升。
- 測試要求見 `ODP-UX-05 §18`（axe 掃描、鍵盤導覽、screen reader spot check、對比度、chart/map fallback）。

---

## 10. Implementation Directives for LLM Workers

這一節是給後續前端實作 worker 的**硬性指令**。違反任一條，PR 應被退回。

### 10.1 一律使用 token，不硬編

- 顏色、字級、字重、行高、間距、圓角、陰影、z-index、動畫時間：**全部走 `ODAY_PLUS_DESIGN_TOKENS.md` 的 semantic token**。
- 若用 Tailwind，顏色必須映射 design token，不得使用任意色碼（見 `ODP-UX-05 §2.2`）。
- 缺少需要的 token 時：**新增 token 並更新 token 文件**，不要在元件就地硬編。

### 10.2 一律使用既有元件契約

- 先到 `ODAY_PLUS_COMPONENT_CONTRACTS.md` 找對應元件；存在就用，不重造。
- 元件 props / states / variants / a11y 以契約文件為準。新需求先擴充契約，再實作。
- Domain 元件（HeatZoneScoreCard、SiteScoreReportSummary、ForecastBandChart、FourLightBadge…）的必備欄位不得刪減。

### 10.3 決策與不確定性表達不得省略

- 預測/估值頁必須顯示 P10/P50/P90 + confidence + data freshness + model version（依適用）。
- 高風險操作（approval / override / export / price / netplan / valuation / model release / rollback / data quality override）**不得 optimistic update**，且必須呼叫後端建立正式 Audit（見 `ODP-UX-05 §14.4`）。
- Decision 區塊必須分離 prediction / recommendation / human decision / execution / outcome。

### 10.4 權限與敏感資料

- 前端權限只用於顯示與操作限制；最終權限由後端判斷。無權限項目不顯示操作按鈕，必要時以唯讀模式呈現摘要。
- 敏感欄位（交易金額、加盟主個資、會員資料、精細成本、估值底價）預設遮罩；依後端 field permission 顯示 `visible/masked/aggregated/hidden`，前端不得自行假設可見。

### 10.5 不得發明

- 不發明狀態名稱、狀態色、新的密度檔位、新的資訊層級順序。
- 不發明 Modal-裝大報告、顏色當唯一訊號、閃爍 critical alert 等被禁止的模式。
- 不把模型/決策邏輯寫死在前端（`ODP-UX-05 §22`）。

---

## 11. Governance

### 11.1 Token / Component lifecycle

Token 與元件生命週期：`PROPOSED → EXPERIMENTAL → APPROVED → DEPRECATED → REMOVED`（見 `ODP-UX-02 §14`）。

- 新 token / 新元件先進 `PROPOSED`，於本系列文件登記後才可在產品碼使用。
- Breaking change（改 token 值語意、刪元件 prop、改狀態語意）需建 ADR 並列出受影響頁面。

### 11.2 三份文件的關係

```text
ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md   方向 / 規則 / 個性 / 實作指令   ← 本文件
ODAY_PLUS_DESIGN_TOKENS.md          token 具體值（唯一值來源）
ODAY_PLUS_COMPONENT_CONTRACTS.md    元件契約（唯一契約來源）
```

任何衝突以「值看 token 文件、契約看 component 文件、規則看本文件」解決；三者不一致時須在同一個 PR 內一起修正。

### 11.3 與正式 UX 文件的關係

本系列不取代 `ODP-UX-01` ～ `ODP-UX-05`。當正式 UX 文件更新時，本系列須同步；當本系列補上的具體值與正式文件衝突時，以正式 UX 文件的**語意與規則**為準，本系列負責提供**可實作的具體值**。

---

## 12. 驗收條件

本文件作為 R0 Design Gate 交付物，需滿足：

- 定義了「營運決策平台」而非行銷頁的產品個性，含明確 Do / Do-NOT 清單。
- 收斂了 status / risk / confidence / model 的統一狀態語言，且明訂「顏色不得作為唯一訊號」。
- 定義了 app shell、page header、七層資訊層級、三檔密度、responsive 規則。
- 給出 LLM worker 的硬性實作指令（token-only、component-contract-only、不確定性必顯、權限/敏感資料、不得發明）。
- 與 `ODAY_PLUS_DESIGN_TOKENS.md`、`ODAY_PLUS_COMPONENT_CONTRACTS.md` 互相引用且不矛盾。
- 可被前端據以建立 token、component、storybook 與視覺回歸測試（`ODP-UX-05 §17`）。
