# ODay Plus Canonical Schema Baseline

## 1. 文件目的與引用說明
本文件定義 ODay Plus 平台之 Canonical Data Model 物理資料庫 Schema 基線與 Migration 規範。本基線設計與 Alembic 遷移腳本依據以下文件設計：
- **`ODP-DATA-04_CANONICAL_DATA_MODEL.md` (Canonical Data Model)**
- **`ODP-SD-05_DATABASE_AND_STORAGE_DESIGN.md` (資料庫與儲存設計)**

本基線已實作為 Alembic 遷移與對應之 PostgreSQL DDL 腳本，佈署於 `infra/db/migrations/`。

---

## 2. 欄位與設計慣例 (Schema Conventions)
為了滿足資料傳輸、稽核追蹤與時序分析的要求，所有資料表在實作時皆嚴格遵循以下物理設計慣例：

### 2.1 識別碼規範 (ID Primitives)
- **平台 ID (`{entity}_id`)**：一律使用由 ODay Plus 平台產生的 UUID 格式，並設為 Primary Key。預設使用 `uuid-ossp` 擴充套件之 `uuid_generate_v4()`。
- **來源 ID (`source_{entity}_id` 或 `source_id`)**：用以保留上游業務系統（如 Legacy Cloud、IoT 網關）之原始主鍵。平台 ID 與來源 ID 徹底分離，避免上游 ID 變更或跨租戶重複時造成的主鍵衝突。

### 2.2 時間與 Ingestion 語意 (Temporal Metadata)
- **`event_time`**：事件或交易實際發生之業務時間（含時區 `TIMESTAMP WITH TIME ZONE`）。
- **`observation_time`**：系統或網關首次觀測到、接收到該事件之時間。
- **`ingested_at`**：資料進入 ODay Plus 整合層 (Integration Layer) 並寫入資料庫之時間，預設為 `CURRENT_TIMESTAMP`。
- **時區**：所有 timestamp 欄位在物理儲存上必須附帶時區，以確保點對點時間正確性與防止時序漏洞。

### 2.3 狀態與型別約束 (Enums & Validation)
- 狀態欄位皆以小寫蛇形命名（如 `store_status`, `listing_status`），並在程式碼與檢驗合約中對齊 `DATA-04` 定義之狀態代碼。

---

## 3. Schema 邏輯分區與核心實體
本基線在 PostgreSQL 中規劃了以下 11 個 Logic Schemas，實作對應之 Canonical Entities：

### 3.1 `core` 模式
存放多租戶、品牌、門市、設備等最基礎之 Master Data：
- **`core.tenants`**：多租戶主檔。
- **`core.brands`**：品牌主檔，定義 `owned` (直營), `franchise` (加盟), `competitor` (競店) 等。
- **`core.address_locations`**：正規化地址與精確經緯度。包含 PostGIS Geometry 空間點索引。
- **`core.stores`**：門市主檔。支援 `store_status` 狀態與 SCD2 有效期間限制（`effective_from`, `effective_to`）。
- **`core.machines`**：門市設備與機台主檔。
- **`core.transactions`**：門市交易事实。
- **`core.machine_cycles`**：機台運作 Cycle 詳情（IoT 網關上傳）。
- **`core.machine_status_events`**：設備狀態改變事件。
- **`core.work_orders`**：工單與維修記錄。
- **`core.customer_service_cases`**：客服客訴案件。

### 3.2 `geo` 模式
存放地理與空間網格資料：
- **`geo.h3_cells`**：Uber H3 空間网格索引。
- **`geo.pois`**：POI 點位資料。
- **`geo.competitor_stores`**：競爭對手門市點位與估計容量。

### 3.3 `expansion` 模式
展店與評估流程實體：
- **`expansion.listings`**：外部房源融合後的 Listing 物件。
- **`expansion.candidate_sites`**：由房源或手動評估轉化之候選開店點。
- **`expansion.heatzone_scores`**：HeatZone 地理网格雷達分數。
- **`expansion.site_score_runs`**：SiteScore 模型預測執行歷程。

### 3.4 `learning` & `workflow` 模式
機器學習特徵、預測、決策與工作流狀態：
- **`learning.model_versions`**：註冊之 ML 模型版本與狀態。
- **`learning.prediction_runs`**：預測批量 Run 歷程。
- **`learning.predictions`**：模型預測出的具體值（如 P10/P50/P90 分位數）。
- **`workflow.decisions`**：系統建議或人工作出之決策。
- **`workflow.approvals`**：決策之審批簽核鏈。

### 3.5 `operations` 模式
營運監控、預警與干預實體：
- **`operations.forecast_outputs`**：營收與利用率預測 Trajectory。
- **`operations.alerts`**：四燈預警 Anomalies。
- **`operations.interventions`**：調價、推廣、維護等干預措施。
- **`operations.intervention_outcomes`**：干預成效（增量營收與毛利）因果評估。

### 3.6 `asset` & `network` 模式
門市估值與網路規劃：
- **`asset.valuation_runs`**：估值模型執行歷程與 P10/P50/P90 估值報告。
- **`network.network_plans`**：店網優化 Solver 求解方案。
- **`network.network_plan_actions`**：Solver 規劃的 OPEN/KEEP/MOVE/EXIT 季執行清單。

### 3.7 `audit` 模式
- **`audit.audit_events`**：重要變更與高風險操作（如核准、模型發布、資料匯出）之完整稽核軌跡。
- **`audit.data_snapshots`**：用於模型訓練或 PIT 正確性校正之資料快照索引。

---

## 4. 索引與空間查詢優化 (Indexes & PostGIS)
- **地理空間查詢**：`core.address_locations` 的 `geom` 欄位與 `geo.h3_cells` 的 `geom` 欄位皆建立 `GIST` 空間索引，以加速等時圈與距離計算。
- **H3 查詢**：各空間主檔與評估表皆針對 `h3_index` / `h3_res_9` 建立 B-Tree 索引，以優化網格層級的 Aggregations。
- **時序查詢**：針對 transactions, cycles, predictions, audit_events 等資料表，對 `event_time`/`occurred_at` 複合實體 ID 建立複合索引。

---

## 5. 資料庫 Migration 策略
1. **工具基線**：採用 **Alembic** 作為 Python/SQL 資料庫 Schema 變更的權威管理工具。
2. **基線版本**：版本 `0001` (由 `infra/db/migrations/versions/0001_baseline.py` 承載) 會完整執行 PostgreSQL 基線 DDL，並初始化上述 11 個 Logical Schemas。
3. **安全防護**：
   - 任何變更均須由 Alembic 腳本封裝，嚴禁對資料庫進行手動 DDL 變更。
   - 所有 Production Schema 變更在套用前，必須於 Staging 進行 `dry-run` 驗證。
   - 下游 model-ready views 必須通過 dbt lineage 隔離，不直接曝露 raw 或未封裝 schema 給外部服務。
