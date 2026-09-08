# H03 — 品牌轉移最小資料請求

- Task: `ODP-BRAND-TRANSFER-CONTRACT-PREP-001`
- 決策來源: D16 (ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md §2.3)
- 使用者選擇: A — 實作／補齊真實資料與契約
- 對象: Commercial Strategy Lead / Data Platform (Market Intelligence Lead)
- 優先級: 工程準備已完成 (Phase A)，本請求回覆後方可進入 Phase B 實作
- 基準代碼: `9048161e058becff5a53593a773d3c42238213fb`

---

## 1. 背景摘要

`ODP-FR-SITE-001` 要求 SiteScore 組合 External Demand、**Brand Transfer**、Format Conversion、Ramp、Seasonality 五項因子。其中 Brand Transfer 目前處於 `BLOCKED_BY_EVIDENCE` 狀態：

- Repo 內**無任何真實品牌轉移資料來源**。
- `brand_transfer_view.sql` 為 CROSS JOIN 合成假視圖，硬編碼 `transfer_ratio = 0.15`、`data_quality_score = 1.0`。
- SiteScore 評分引擎**從未接入任何品牌轉移欄位**（`SiteScoreFeatureInput` 無相關 field）。

使用者已確認選擇「A：實作／補齊真實資料與契約」(D16)。工程方已完成：
- 契約草案 ([contract-draft.json](contract-draft.json))
- 欄位字典 ([field-dictionary.md](field-dictionary.md))
- 來源至消費端路徑圖 ([source-consumer-map.json](source-consumer-map.json))

以下為進入 Phase B 實作所需的最小資料請求。

---

## 2. 最小資料請求

### 2.1 核心問題：資料來源

| # | 請求項目 | 說明 | 回覆格式 |
|---|---|---|---|
| H03-Q1 | **資料來源識別** | 公司目前是否擁有或可取得跨品牌消費者轉移資料？例如：會員跨店交易日誌、外部消費面板（發票載具）、第三方市調資料、POS 跨品牌忠誠度交叉比對。 | 有 / 無 / 規劃中 → 若有，請說明來源名稱與提供者 |
| H03-Q2 | **資料負責人** | 誰是該資料來源的業務負責人（Source Owner）？ | 角色 + 姓名或組織 |
| H03-Q3 | **授權範圍** | 該資料是否可用於平台內部分析與評分？是否有商用限制、地域限制或使用期限？ | 授權摘要 |

### 2.2 資料規格（若有來源）

| # | 請求項目 | 說明 | 回覆格式 |
|---|---|---|---|
| H03-Q4 | **欄位與格式** | 資料包含哪些欄位？是否包含 source brand、target brand、轉移量、觀測期間、地理範圍？ | 欄位清單或範例 schema |
| H03-Q5 | **量測方法** | 轉移率如何計算？是基於消費面板、忠誠度交叉、POS 配對、問卷或模型估計？ | 方法名稱 + 簡述 |
| H03-Q6 | **樣本規模** | 典型觀測期間的樣本數量級（如每月 N 筆交易對、M 個商圈覆蓋）？ | 數量級估計 |
| H03-Q7 | **更新頻率** | 資料預期多久更新一次？有無 SLA 保證？ | 頻率 + SLA 有/無 |
| H03-Q8 | **資料鮮度** | 最近一次可取得的資料觀測日期？距今多少天？ | 日期或「未知」 |

### 2.3 取得方案（若無現成來源）

| # | 請求項目 | 說明 | 回覆格式 |
|---|---|---|---|
| H03-Q9 | **外部採購候選** | 是否有已接洽或可考慮的外部資料供應商？ | 供應商名稱或「未有」 |
| H03-Q10 | **替代資料方案** | 是否可接受以下替代方案作為過渡？(a) 會員跨品牌交易日誌內部分析 (b) 門市鄰近競品顧客調查 (c) 第三方 footfall 數據 (d) 以上皆無 | 選擇 a/b/c/d |
| H03-Q11 | **時程預期** | 如需外部採購，預期多久可取得穩定資料交付？ | 預估月份或「未知」 |

---

## 3. Phase B 驗收矩陣

Phase B（真實資料實作）的入場與驗收條件如下。**H03 回覆完成 ≠ Phase B 驗收通過**；實際驗收需要端對端資料流驗證。

| 驗收項目 | 入場條件 (Gate-in) | 驗收標準 (Gate-out) |
|---|---|---|
| **來源接入** | H03-Q1 回覆「有」且 Q2/Q3 已確認 | Raw data 可通過 ingestion pipeline 寫入持久層，pipeline 有 run ID 與時戳 |
| **Schema 遷移** | 契約草案定稿為 v1.0.0 | `core.brand_transfer_observations` (或等價) migration 建立並通過 CI |
| **dbt 視圖改寫** | 上游持久表有數據 | `brand_transfer_view.sql` 改為查詢真實上游表，移除 CROSS JOIN 與固定常數 |
| **消費端接入** | dbt 視圖產出真實資料 | `SiteScoreFeatureInput.brand_transfer_ratio` 欄位新增且評分邏輯消費該欄位 |
| **反事實測試** | 消費端接入完成 | 不同品牌轉移率商圈產生顯著不同評分；缺值不產生滿分確定性 |
| **品質防線** | 以上全部 | `missing` ≠ `observed_zero`；`stale` 被 freshness gate 攔截；`insufficient_sample` 降低 confidence |

### 3.1 四類資料缺失的區分要求

| 缺失類型 | 定義 | 合約表示 | 消費端行為 |
|---|---|---|---|
| `missing` | 無任何資料來源可量測此品牌對 | 記錄不存在或 `transfer_ratio = null` | Fail-closed：標記 `unmodelled_brand_transfer` |
| `stale` | 觀測存在但已超過 `staleness_threshold_days` | `is_scoring_eligible = false` | 降級或排除 |
| `insufficient_sample` | 觀測存在但 `sample_size` 低於門檻 | `confidence = null` 或低於最低值 | 降低輸出信心度 |
| `observed_zero` | 確實量測到零轉移 | `transfer_ratio = 0.0`, `sample_size > 0`, `confidence > 0` | 合法零值 — 可用於評分 |

> **關鍵原則**：合成資料只能在明示為 fixture 的測試中使用。固定 `0.15` / `1.0` 不能充當真實 producer。

---

## 4. 本請求不包含的項目

- 不請求採購決策或預算核准 — 僅請求資料來源識別
- 不代替正式法務合約審查
- 不授權啟用任何外部資料抓取
- 不建立 waiver 或 `--ignore-vuln`
- 不偽造 Human/Ops 身分或法務簽章
- Format Conversion (WP-31A) 的資料請求由另一 task 處理，不在此 H03 範圍內

---

## 5. 回覆方式

請將回覆提供給 Platform Architecture Board 或在本 repo 的相應 task 討論中回覆。回覆後工程方將：
1. 確認來源契約與欄位對照
2. 定稿 schema 契約 v1.0.0
3. 建立 ingestion pipeline 與 migration
4. 改寫 `brand_transfer_view.sql`
5. 接入 SiteScore 消費端
6. 執行端對端反事實測試

**回覆截止建議**：2026-10-01（與 HB-SITE001-BRAND-TRANSFER-001 下次檢視日期對齊）
