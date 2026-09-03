# SITE-001 Brand Transfer 與 Format Conversion 資料準備度查證報告

- 日期：2026-09-03
- 任務：`ODP-SITE001-DATA-READINESS-001`
- 擁有者：Antigravity4
- 審查者：Claude2
- 階段：ODP Remediation · W0 Evidence（第 0 批：資料源確認）
- 依據與來源：
  - [修正計畫](../plans/ODP_REMEDIATION_PLAN_2026-09-03.md)
  - [待裁決事項](../plans/ODP_OPEN_DECISIONS_2026-09-03.md)
  - [結構性成因處理結果](ODP_STRUCTURAL_REMEDIATION_2026-09-01.md)
  - [FR 查證報告](ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md)

---

## 1. 執行摘要

本報告針對 `ODP-FR-SITE-001`（「系統必須組合 External Demand、Brand Transfer、Format Conversion、Ramp、Seasonality」）中尚未實作的兩個成員 —— **Brand Transfer（既有品牌客群移轉）** 與 **Format Conversion（店型轉換業務事件）** 進行資料存在性、權威來源、生產者、模型就緒視圖及消費端全鏈路查證。

### 查證核心結論

1. **Brand Transfer（既有品牌客群移轉）**：評定為 **`BLOCKED_BY_EVIDENCE`**。
   - 既有 repo 中的 `brand_transfer_view.sql` 僅為基於 `core.brands` 的笛卡兒積（CROSS JOIN）合成假視圖，硬編碼固定轉移率 `transfer_ratio = 0.15` 與滿分信心值；`MODEL_READY_VIEWS_BASELINE.md` 已明確載明此為 mock baseline。
   - 上游無任何真實會員客群流轉、交易跨品牌重疊或外部調研數據生產者；SiteScore 消費端亦未接入該欄位。
2. **Format Conversion（店型轉換業務事件）**：評定為 **`BLOCKED_BY_EVIDENCE`**。
   - 業務實務上目前皆為新店開店（Greenfield），尚無營運中門市進行改裝轉型（Brownfield Conversion）的實際業務事件與履歷資料。
   - `TargetFormatRegistry` 僅依門市坪數挑選目標店型（選店型，非轉店型）；`simulator.py` 缺乏轉換資本支出、停業過渡期營收損失與客群保留模型；資料庫無轉換事件表。
   - 名詞「conversion」在前端與擴展流中多指「Listing to Candidate conversion」（物件轉候選點之工作流狀態轉換），非門市店型改裝轉換。

依據 Remediation Plan 第 0 批處置規則，兩項皆**不得**以 fixture 或 placeholder 宣稱 `IMPLEMENTATION_READY`，亦不應在缺乏資料餵養的情況下排入第 6 批程式碼開發，而應以 `BLOCKED_BY_EVIDENCE` 記錄明確 Owner、檢視週期與重啟條件。

---

## 2. 查證方法與防偽原則

本次查證遵循以下嚴格準則：
1. **拒絕子字串假陽性**：不以全樹 grep 搜尋「transfer」或「conversion」之命中數充當證據，逐一核對實體定義、資料表 DDL、dbt 轉換邏輯與 Python dataclass 欄位。
2. **拒絕假視圖／Mock 充當生產就緒**：嚴格區分「SQL view 語法存在」與「底層具備真實資料源」；硬編碼常數或 cross-join 生成之 baseline 視圖不得視為資料已準備就緒。
3. **逐層端到端追溯**：自來源端（Source Provider）→ 持久層（PostgreSQL Migration）→ dbt 就緒層（Model-Ready View）→ 領域層（Domain Feature Input）→ 消費 API 與決策輸出端逐層檢驗。

---

## 3. Member 1：Brand Transfer（既有品牌客群移轉）查證

### 3.1 需求定義與業務意涵
量化當目標商圈引入 ODayPlus 門市或競品門市異動時，消費者在既有品牌／姊妹品牌／競品品牌間的轉移比例與客群流動率（Cross-brand migration ratio），以修正預估需求。

### 3.2 全鏈路追溯與存在性查證

| 層級 | 檢查目標與路徑 | 實際查證結果 | 判斷 |
|---|---|---|---|
| **來源生產者 (Producer)** | 外部消費面板 / POS 跨品牌交易日誌 / 會員跨店足跡 | 全樹及外部接入器中**無任何生產者**，無外部資料廠商（如發票載具、市調面板）接入。 | **不存在** |
| **持久層 (PostgreSQL)** | `infra/db/migrations/000001_baseline_canonical_schema.sql:38`<br>`000002_data_domain_canonical_entities.sql:26`<br>`000004_durable_product_domain.sql:12` | 僅有 `core.brands` 表（欄位：`brand_id`, `brand_code`, `brand_name`, `brand_type`, `brand_capture_group`, `status`）。僅存品牌靜態主檔，**無任何客群流動、轉移機率或交易矩陣欄位**。 | **僅靜態主檔** |
| **dbt 就緒層 (Model-ready)** | `pipelines/dbt/models/model_ready/brand_transfer_view.sql`<br>`docs/data/MODEL_READY_VIEWS_BASELINE.md:21-25` | `brand_transfer_view.sql` 以 `core.brands b1 CROSS JOIN core.brands b2` 產生品牌對，硬編碼：<br>• `transfer_ratio = 0.15`<br>• `data_quality_score = 1.0`<br>• `confidence = 1.0`<br>• `store_format_code = 'ODAY_G2'`<br>文件明確註記：「`brand_transfer_view` wired using baseline core tables as safe, predictable mock baselines」。 | **合成 Mock** |
| **信號存儲 (Signal Store)** | `services/signal-store/client.py:319` | 僅在範例 mock payload 中出現 `"brand_transfer_confidence": 0.76`，無任何產品模組引用此 client。 | **假樣本** |
| **領域消費端 (SiteScore)** | `modules/sitescore/domain/scoring.py`<br>`modules/sitescore/v3/application/service.py` | `SiteScoreFeatureInput` 與評分計算邏輯完全沒有 `brand_transfer` 相關欄位或特徵輸入；評分模型從未消費 `brand_transfer_view`。 | **未接入** |

### 3.3 獨立處置（Disposition Record）

```yaml
requirement_id: ODP-FR-SITE-001
member: BRAND_TRANSFER
disposition_status: BLOCKED_BY_EVIDENCE
source_owner: Commercial Strategy / Data Platform (Market Intelligence Lead)
schema_status: ABSENT (core.brands contains master metadata only; no migration matrix)
freshness: N/A (no streaming or scheduled batch producer)
sample_lineage: core.brands (metadata) -> brand_transfer_view (synthetic cross-join with fixed 0.15 ratio) -> unconsumed
next_review_date: 2026-10-01
reopen_trigger: >-
  External consumer panel feed (e.g. receipt/panel provider) or cross-brand POS loyalty dataset
  is formally contracted and ingested into raw data platform with versioned schema and SLA.
```

---

## 4. Member 2：Format Conversion（店型轉換業務事件）查證

### 4.1 需求定義與業務意涵
針對營運中既有門市進行店型變更（例如：自一代店升級為 ODAY_G2 標準店、縮減為 G3 Compact、或擴充為 Flagship 旗艦店），計算其改裝資本支出（Remodeling Capex）、停業裝修期間之營收中斷損失、轉型後爬坡曲線（Ramp-up）及投資回收期（Payback）。

### 4.2 全鏈路追溯與存在性查證

| 層級 | 檢查目標與路徑 | 實際查證結果 | 判斷 |
|---|---|---|---|
| **業務事件 (Business Event)** | 門市改裝轉型專案 / 營運歷史紀錄 | 實務營運目前全為 Greenfield 新設展店，**從未發生店型轉換業務動作**，無任何改裝歷史數據或營運事件。 | **無業務事件** |
| **持久層 (PostgreSQL)** | `core.stores` 表（`store_format_code`） | `core.stores` 僅記錄門市當前靜態店型代碼（`store_format_code VARCHAR(100)`），**無 `store_format_conversions`、`renovations` 或歷史變更履歷表**。 | **無事件記錄** |
| **店型規格與註冊表** | `modules/site_economics/domain/formats.py:400-444` (`TargetFormatRegistry`) | `find_best_format_for_area(area_ping)` 僅依門市坪數推薦新設店型（<20 坪 G3_COMPACT，20-35 坪 G2，>35 坪 FLAGSHIP）。**此為新店店型選定（Selection），非現存店型轉換（Conversion）**。 | **新店選型非轉換** |
| **財務模擬引擎** | `modules/site_economics/domain/simulator.py:28-85` (`SimulationInput`) | 模擬引擎僅計算新設門市之設備支出、裝修費、標準爬坡與折現現金流。**完全無改裝停業損失、舊機殘值折抵、改裝額外成本等轉換計算路徑**。 | **無轉換模型** |
| **名詞碰撞查證** | `apps/web/features/operator/NetworkFindAreasWorkspace.tsx:483`<br>`docs/design/ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md:159`<br>`docs/evidence/completion/ODP-OC-R4-005/` | 程式碼中多處出現 `conversion`，經核對全為 **`Listing-to-Candidate conversion`（房源物件轉為評估候選點之流程狀態推進）**，與實體店型轉換完全無關。 | **子字串名詞碰撞** |

### 4.3 獨立處置（Disposition Record）

```yaml
requirement_id: ODP-FR-SITE-001
member: FORMAT_CONVERSION
disposition_status: BLOCKED_BY_EVIDENCE
source_owner: Store Operations / Real Estate Expansion & Finance (Site Economics Lead)
schema_status: ABSENT (no format conversion event or renovation history table in schema)
freshness: N/A (no operational event stream)
sample_lineage: TargetFormatRegistry (static area mapping) -> simulator.py (greenfield only) -> zero conversion paths
next_review_date: 2026-10-01
reopen_trigger: >-
  Store Operations formally approves a brownfield format conversion playbook with remodeling cost
  schedules; schema migration introduces store format transition event tables and tracking.
```

---

## 5. 對治理清單與 Remediation Plan 之影響

1. **`set_valued_requirements.json`**：
   - `ODP-FR-SITE-001` 中的 `BRAND_TRANSFER` 與 `FORMAT_CONVERSION` 保持 `absent` 狀態，其註記與本報告結論一致。
2. **Remediation Plan（第 0 批 → 第 6 批處置）**：
   - 依據計畫規則：「若答案是資料不存在，這兩項走正式需求修訂或具期限的 waiver／risk acceptance，先做才不會排到假工作」。
   - **結論**：第 6 批實作清單中，`SITE-001 Brand Transfer` 與 `SITE-001 Format Conversion` **不進入實作開發**，避免建立裝飾性功能或偽造假模型，直至滿足上述 Reopen Trigger 為止。

---

## 6. 查證指令收據（Reproducibility Receipts）

以下指令於工作目錄執行可完整重現上述查證事實：

```bash
# 1. 驗證治理清單成員狀態與檢查工具通過
uv run --python 3.12 pytest delivery_toolchain/governance/test_check_requirement_members.py

# 2. 查證 brand_transfer_view 硬編碼固定常數與 mock 宣告
grep -n -C 5 "transfer_ratio" pipelines/dbt/models/model_ready/brand_transfer_view.sql
grep -n -C 3 "brand_transfer_view" docs/data/MODEL_READY_VIEWS_BASELINE.md

# 3. 查證 TargetFormatRegistry 僅作坪數店型挑選，無店型轉換邏輯
python3 -c "from modules.site_economics.domain.formats import DEFAULT_FORMAT_REGISTRY; print(DEFAULT_FORMAT_REGISTRY.list_codes()); print(DEFAULT_FORMAT_REGISTRY.find_best_format_for_area(25.0).format_code)"
```
