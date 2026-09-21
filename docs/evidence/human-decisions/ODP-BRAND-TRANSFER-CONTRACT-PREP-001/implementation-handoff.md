# Brand Transfer — Implementation Handoff

- Task: `ODP-BRAND-TRANSFER-CONTRACT-PREP-001`
- Phase: A — Engineering preparation (contract, source map, H03 request)
- Status: Phase A deliverables complete; Phase B blocked on H03 response
- Inspected source: `9048161e058becff5a53593a773d3c42238213fb`
- Inspected at: 2026-09-08T15:23Z

---

## 1. Phase A 交付摘要

本 task 完成 WP-30A（品牌轉移契約草案與 H03 準備）的 Phase A 工程準備：

| 交付物 | 檔案 | 內容 |
|---|---|---|
| **契約草案** | [contract-draft.json](contract-draft.json) | JSON Schema 定義品牌轉移資料的所有欄位、型別、約束與反模式規則 |
| **欄位字典** | [field-dictionary.md](field-dictionary.md) | 逐欄位說明來源、現狀、需資料 owner 確認的項目 |
| **來源消費端路徑圖** | [source-consumer-map.json](source-consumer-map.json) | 6 層端對端追溯：外部生產者 → 持久層 → dbt → Signal Store → SiteScore → 文件 |
| **H03 資料請求** | [human-input-request-H03.md](human-input-request-H03.md) | 最小資料請求、取得方案、Phase B 驗收矩陣 |
| **實作交接** | 本文件 | Phase B 入場條件、任務邊界與已知風險 |
| **README** | [README.md](README.md) | 本 task 產物索引 |

---

## 2. 源碼盤點結論

### 2.1 現有元件與其角色

| 元件 | 路徑 | 角色 | 可用性 |
|---|---|---|---|
| Brand master table | `infra/db/migrations/000001_baseline_canonical_schema.sql` → `core.brands` | 靜態品牌主檔（brand_id, brand_code, brand_name, brand_type） | ✅ 可用作品牌參考 |
| Competitor stores | `infra/db/migrations/000001_baseline_canonical_schema.sql` → `geo.competitor_stores` | 競品門市位置（brand_name 為文字，未連結 core.brands） | ⚠️ 部分可用 |
| dbt brand transfer view | `pipelines/dbt/models/model_ready/brand_transfer_view.sql` | CROSS JOIN 合成假視圖，hardcode `transfer_ratio = 0.15` | ❌ Mock，不可用於生產 |
| Signal Store mock | `services/signal-store/client.py:319` | 範例 payload 含 `brand_transfer_confidence: 0.76` | ❌ Mock，未被任何模組消費 |
| SiteScore consumer | `modules/sitescore/domain/scoring.py` | `SiteScoreFeatureInput` dataclass | ❌ 無任何品牌轉移欄位 |

### 2.2 不存在的元件

| 缺失元件 | 所需位置 | 說明 |
|---|---|---|
| 品牌轉移觀測持久表 | `core.brand_transfer_observations` 或等價 | 儲存真實跨品牌消費轉移觀測 |
| 外部資料生產者 / ingestion pipeline | `pipelines/` 或 `services/` | 從外部來源擷取轉移資料 |
| SiteScore brand_transfer_ratio 欄位 | `modules/sitescore/domain/scoring.py` | 消費端接入品牌轉移特徵 |
| 反事實測試 | `tests/` | 驗證轉移率變化對評分的影響 |

---

## 3. Phase B 入場條件

Phase B（WP-30B：真實資料實作）**必須**滿足以下全部條件方可啟動：

| # | 條件 | 責任方 | 狀態 |
|---|---|---|---|
| 1 | H03 資料請求已回覆：真實品牌轉移來源已識別 | Commercial Strategy / Data Platform Lead | ⏳ 待回覆 |
| 2 | 資料 ownership 與授權範圍已確認 | Source Owner (H03-Q2, Q3) | ⏳ 待確認 |
| 3 | 統計定義已由資料 owner 確認（量測方法、最小樣本、信心區間） | Source Owner (H03-Q5, Q6) | ⏳ 待確認 |
| 4 | Schema 契約已從 draft 定稿為 v1.0.0 | 工程方 + Source Owner | ⏳ 依賴 #1-3 |
| 5 | Source ingestion 權限已取得 | Ops / Source Owner | ⏳ 依賴 #1 |

### 3.1 禁止事項

- ❌ 不得將 `brand_transfer_view.sql` 的合成資料接入 SiteScore 生產路徑
- ❌ 不得將固定 `0.15` 或 `1.0` 常數偽裝為真實量測
- ❌ 不得在 H03 回覆前逕自決定資料來源或採購
- ❌ 不得建立 waiver 或 exception 繞過資料缺失
- ❌ 不得偽造 Human/Ops 簽署

---

## 4. Phase B 實作指引（待入場條件滿足）

Phase B 承接任務在入場條件滿足後，應按以下順序執行：

1. **Migration**: 建立 `core.brand_transfer_observations` 持久表（參考 [contract-draft.json](contract-draft.json) 欄位定義）
2. **Ingestion pipeline**: 建立從已確認來源到持久表的 ETL/ingestion
3. **dbt 視圖改寫**: 將 `brand_transfer_view.sql` 從 CROSS JOIN mock 改為查詢真實上游表
4. **消費端接入**: 在 `SiteScoreFeatureInput` 新增 `brand_transfer_ratio: float | None = None` 欄位
5. **評分邏輯**: 實作 fail-closed 棄權原則 — 缺值不給滿分
6. **反事實測試**: 建立兩組測試 (a) 不同轉移率 → 不同評分 (b) 缺值 → 降級標記
7. **freshness gate**: 實作 `staleness_threshold_days` 檢查，過期觀測不用於評分

---

## 5. 與其他工作包的關係

| 工作包 | 關係 | 協調需求 |
|---|---|---|
| WP-31 (FORMAT_CONVERSION) | 同屬 ODP-FR-SITE-001 | 共享 SiteScore 評分消費端；兩者串行修改 `scoring.py` |
| WP-90 (整合驗收) | 依賴 Phase B 完成 | 品牌轉移的真實 producer/consumer 證據才能進入整合驗收 |
| HB-SITE001-BRAND-TRANSFER-001 | 前置處置單 | 本 task 的工程準備回應該 handback 的 pathway_a |

---

## 6. 已知風險

| 風險 | 影響 | 緩解措施 |
|---|---|---|
| 無外部資料來源可取得 | Phase B 無法啟動 | H03 包含替代方案選項；最終可能需走 pathway_b (formal amendment) |
| 資料規格與契約草案不符 | 需修改 schema | 契約草案已設計為可擴展；各欄位有 null 選項 |
| 統計定義爭議 | 延遲契約定稿 | 欄位字典已列出需確認項目，加速對齊 |
| competitor_stores.brand_name 與 core.brands 不連結 | 跨品牌分析需人工對照 | 未來 migration 應建立 FK 或 mapping 表 |

---

## 7. 證據採集記錄

| 命令 | Source SHA | 執行時間 (UTC) | Exit Code | 結果摘要 |
|---|---|---|---|---|
| `rg -n "BRAND_TRANSFER\|brand_transfer" modules pipelines packages models` | `9048161e` | 2026-09-08T15:23Z | 0 | 2 matches: brand_transfer_view.sql:11, schema.yml:606 |
| `cat pipelines/dbt/models/model_ready/brand_transfer_view.sql` | `9048161e` | 2026-09-08T15:23Z | 0 | CROSS JOIN mock with hardcoded 0.15 |
| `grep -n "brand" infra/db/migrations/000001_baseline_canonical_schema.sql` | `9048161e` | 2026-09-08T15:23Z | 0 | core.brands static table, geo.competitor_stores |
| `grep -rn "brand_transfer" modules/sitescore/` | `9048161e` | 2026-09-08T15:23Z | 0 | 0 matches — SiteScore has no brand transfer fields |
| `grep -rn "brand_transfer_confidence" services/signal-store/client.py` | `9048161e` | 2026-09-08T15:24Z | 0 | Line 319: mock payload only |
