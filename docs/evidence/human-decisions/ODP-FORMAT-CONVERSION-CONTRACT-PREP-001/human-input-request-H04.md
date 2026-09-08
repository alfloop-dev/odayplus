# H04 — 店型轉換（Format Conversion）事件與財務定義請求

- **請求編號**: H04
- **Task**: `ODP-FORMAT-CONVERSION-CONTRACT-PREP-001`
- **需求**: `ODP-FR-SITE-001 / FORMAT_CONVERSION`
- **請求日期**: 2026-09-08
- **請求人**: Claude（工程準備；task canonical owner）
- **目標回覆人**: Store Operations Lead / Real Estate Expansion & Finance Lead / Site Economics Lead
- **狀態**: AWAITING_HUMAN_INPUT
- **來源**: [ODP 人工決策執行規畫](../../../plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md) §4 H04、§6 WP-31

> [!IMPORTANT]
> 本請求是 Stage B（真實資料實作）的入場條件。Stage A（工程準備：事件契約草案、欄位字典、來源消費者地圖）已完成並交付審查。**在取得以下資料前，FORMAT_CONVERSION 不會進入 production 實作，也不會聲稱 producer 或 runtime 已可用。**

---

## 1. 請求背景

使用者已確認決策 D17（FORMAT_CONVERSION：實作／補齊真實資料與流程）。

Repo 內查證事實（`origin/dev` @ `cf04c046`，2026-09-08）：

- PostgreSQL（`000001`）與 SQLite（`000004`）Schema 均僅記錄靜態 `store_format_code`，無門市改裝或轉型歷程表
- `TargetFormatRegistry` 僅依坪數挑選新設店型（選型而非轉型）
- `simulator.py` 僅模擬 Greenfield 新店經濟效益，無 Brownfield 停業損失、舊機殘值折抵、改裝額外 Capex 與轉型 Ramp 曲線
- 生產代碼路徑中無任何 `format_conversion` 或 `brownfield` 邏輯

詳見：[SITE001 資料準備度報告](../../ODP_SITE001_DATA_READINESS_2026-09-03.md)、[SITE001 處置報告](../../ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md)

---

## 2. 需要提供的資料（逐項）

### 2.1 真實轉型事件

| 項次 | 需要的資料 | 用途 | 備註 |
|------|-----------|------|------|
| 2.1.1 | 過去是否有門市進行過店型轉換（如一代店升級 G2、縮減為 G3 Compact）？ | 確認是否有歷史試點或既有轉型專案紀錄 | 若無歷史轉型資料，依 D17 由 2.1.3 未來規劃事件與業務規格推進，Stage B 保持 waiting_for_data |
| 2.1.2 | 若有，請提供至少一筆歷史轉型案例的基本資料：門市代碼、原店型、新店型、改裝起迄日期 | 建立 migration seed 與測試 fixture | 可提供匿名化版本 |
| 2.1.3 | 未來是否有已規劃或預算編列的轉型專案？ | 確認需求時效性、規劃事件證據、取得負責人與時程 | 依 D17 實作方向收集規劃事件，提供 Stage B 所需資料 |

### 2.2 原店型與新店型定義

| 項次 | 需要的資料 | 用途 | 備註 |
|------|-----------|------|------|
| 2.2.1 | 現有所有店型代碼的完整清單（含非 repo 中的舊型號如 G1） | 校對 `TargetFormatRegistry` 覆蓋範圍 | 目前僅註冊 ODAY_G2, ODAY_G3_COMPACT, ODAY_FLAGSHIP |
| 2.2.2 | 各店型之間允許的轉換路徑（如 G1→G2 可以、G2→G3 不可以） | 建立轉換矩陣驗證規則 | 若無限制則記錄「所有組合均允許」 |

### 2.3 財務與停業規則

| 項次 | 需要的資料 | 用途 | 備註 |
|------|-----------|------|------|
| 2.3.1 | 改裝期間停業天數的估算方式或歷史範圍 | `downtime_days` 欄位定義 | 按店型轉換類型提供（如 G1→G2 約 30-45 天） |
| 2.3.2 | 停業期間營收損失的計算基準（日均、週均、月均？取多少月平均？） | `daily_baseline_revenue` 定義 | 若使用最近 N 月平均，請指定 N |
| 2.3.3 | 改裝資本支出（Capex）的項目組成與估算範圍 | `remodeling_capex` 定義 | 是否包含設計費、許可證費、臨時倉儲？ |
| 2.3.4 | 舊設備／裝潢殘值處理方式（轉售、內部調撥、報廢？估值方式？） | `residual_value` 與 `disposal_cost` 定義 | 若按帳面折舊殘值，請提供折舊方法與年限 |
| 2.3.5 | 改裝後爬坡期（Ramp）的預期長度與曲線形狀 | `ramp_months` 與 `ramp_curve_id` 定義 | 是否與新店開業 ramp 相同？若不同，如何不同？若未提供，模型保持 unquantified，不自動套用 greenfield 預設曲線 |

### 2.4 資料來源與負責人

| 項次 | 需要的資料 | 用途 | 備註 |
|------|-----------|------|------|
| 2.4.1 | 轉型事件的權威資料系統（ERP、專案管理系統、手動紀錄？） | `event_source` 欄位與接入設計 | 確認匯出格式與頻率 |
| 2.4.2 | 財務參數的權威來源（會計系統、資產管理系統？） | 資本支出、殘值資料接入 | 需確認存取權限 |
| 2.4.3 | 各項資料的負責人姓名／角色 | 責任追蹤與後續協調 | 可以是角色名而非個人姓名 |

---

## 3. 31B 驗收案例（待 H04 資料後驗證）

以下驗收案例已在 [事件契約草案](event-contract-draft.json) 與 [來源消費者地圖](source-consumer-map.json) 中定義，列出供回覆時參照：

### 3.1 停業影響驗證

- **單一變數受控比較**：取同一件轉型案，把其餘財務輸入全部固定（改裝 Capex、殘值、處分費、ramp_months、ramp_curve_id、日均基準營收、目標店型、坪數），只改變 `downtime_days`。停業天數增加時，第一年淨現金流必須嚴格下降，投資回收期不得縮短。
- 停業 30 天 vs 停業 0 天（其餘輸入完全相同）的投資回收期差異必須可量化。
- **不採用**「Brownfield 必須劣於 Greenfield 零停業基準」這種跨情境比較作為驗收條件：Capex、殘值與 ramp 等輸入不同時，其效果可以合理蓋過停業損失，該條件會誤判正確的轉型經濟性。

### 3.2 成本與殘值驗證

- 舊設備殘值折抵金額提高時，專案初期淨資本支出必須等額下降
- 殘值與處分費均為 null（未知）時，模擬不得用零替代，必須標注 unquantified

### 3.3 Ramp 影響驗證

- 改裝後 ramp 曲線不同於新店 ramp 時，收入預測必須反映差異
- ramp_months 或 ramp_curve_id 為 null（未知）時，不得使用新店預設 ramp，必須標注 unquantified，避免產生無依據的財務估算

### 3.4 事件回放不重複計費

- **主鍵去重**：以相同 `event_id` 重複送入轉型事件，不得產生重複的成本／營收損失項目。這是唯一永遠生效的去重規則。
- **次鍵去重（有條件）**：以不同 `event_id` 但帶有**相同且為非 null、非空字串**的有效 `idempotency_key` 送入，才視為同一事件。
- **null／未提供不參與去重**：`idempotency_key` 為 `null` 或未提供時，該事件只依 `event_id` 去重，必須與其他事件保持相異。兩筆合法的相異事件即使都帶 `null`，也**絕不可**被判為重複而合併，否則會靜默丟失真實的轉型成本。
- **空字串是驗證錯誤**：`idempotency_key` 為空字串時必須在驗證階段拒絕，不得當成鍵值，也不得正規化成 `null`。
- **31B 新增案例（Stage B 驗證，Stage A 不需實作）**：送入兩筆 `event_id` 相異、`idempotency_key` 皆為 `null` 的事件，以及兩筆同樣相異但省略該欄位的事件，四筆都必須保留為相異事件、成本項目不得合併。

### 3.5 缺資料標示

- 草案**不聲稱 producer 已可用** — 所有 source 均標為 `NOT_AVAILABLE_IN_REPO` 或 `PARTIAL_EXISTS_GREENFIELD_ONLY`
- 缺失欄位（`null`）不等於零值（`0`），在報表中必須明示為「未量化」

---

## 4. 回覆方式

請以以下任一方式回覆：

1. **直接在本文件下方新增回覆段落**（保留原請求內容）
2. **提供獨立文件**並在本請求中標註路徑
3. **在對話中逐項回答**，由工程方記錄至本文件

回覆後，Stage B 工程方將：
1. 更新事件契約（`event-contract-draft.json` → 正式版）
2. 建立 `core.store_format_conversions` migration
3. 擴充 `simulator.py` 支援 `ConversionSimulationInput`
4. 執行 31B 驗收測試並交付驗收收據

---

## 5. 決策 D17 延續與缺資料處理原則

依據執行規畫已確認之決策 **D17（FORMAT_CONVERSION：實作／補齊真實資料與流程）**，本工作包之確定方向為實作而非刪除或豁免。

- **無歷史資料時的處理**：若實體門市過去未曾有過歷史轉型資料，不因此將任務退回需求修訂或豁免；應以未來規劃轉型事件、業務規則手冊與財務參數作為輸入，Stage B 維持 `BLOCKED_BY_EVIDENCE` / `waiting_for_data` 直至規劃資料與負責人到位。
- **資料未齊時的狀態**：Stage A 交付之契約草案已完成工程準備，Stage B 等待 H04 回覆（真實事件證據、取得負責人與時程）。未取得合法資料前，不聲稱 producer 已可用，亦不進入 production 實作。
- **治理變更界線**：除非人類授權人（Store Operations Lead / Architecture Board / Human/Ops）於更高治理層次正式推翻 D17 決策，否則工程任務不主動提出 amendment/waiver，嚴格遵循 D17 實作路徑推進。
