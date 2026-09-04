# SITE-001 Brand Transfer 與 Format Conversion 資料準備度查證報告

- 日期：2026-09-03
- 任務：`ODP-SITE001-DATA-READINESS-001`
- 擁有者：Antigravity4
- 審查者：Codex2
- 階段：ODP Remediation · W0 Evidence（第 0 批：資料源確認）
- 依據與來源：
  - [修正計畫](../plans/ODP_REMEDIATION_PLAN_2026-09-03.md)
  - [待裁決事項](../plans/ODP_OPEN_DECISIONS_2026-09-03.md)
  - [結構性成因處理結果](ODP_STRUCTURAL_REMEDIATION_2026-09-01.md)
  - [FR 查證報告](ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md)

---

## 1. 執行摘要

本報告針對 `ODP-FR-SITE-001`（「系統必須組合 External Demand、Brand Transfer、Format Conversion、Ramp、Seasonality」）中尚未實作的兩個成員 —— **Brand Transfer（既有品牌客群移轉）** 與 **Format Conversion（店型轉換業務事件）** 進行資料準備度、生產者、模型就緒視圖及消費端全鏈路查證。

### 查證核心結論

依據查證原則，本報告嚴格區分 **「Repo 內可證明的技術現狀（未發現 Producer / Schema / 轉換計算路徑）」** 與 **「尚待權威營運／資料平台 Source Owner 證實的業務現狀（外部資料源／實際業務事件）」**，不得將 Repo 外的未知狀態逕自判定為「已證實不存在」：

1. **Brand Transfer（既有品牌客群移轉）**：評定為 **`BLOCKED_BY_EVIDENCE`**。
   - **Repo 內查證事實**：`core.brands` 僅存品牌靜態主檔；`brand_transfer_view.sql` 僅為基於 `core.brands` 的笛卡兒積（CROSS JOIN）合成假視圖，硬編碼固定轉移率 `transfer_ratio = 0.15` 與滿分信心值（`MODEL_READY_VIEWS_BASELINE.md` 明確載明此為 mock baseline）；SiteScore 評分端亦未接入任何轉移欄位。Repo 內無任何真實生產者、Schema 綱要或消費路徑。
   - **業務／資料源待證實事項（Evidence Request）**：Repo 外是否存在離線會員跨店數據、外部市調面板或第三方消費資料合作協議，尚待權威 Source Owner（Commercial Strategy / Data Platform Lead）確認。在取得正式資料規格與接入承諾前，評定為 `BLOCKED_BY_EVIDENCE`。

2. **Format Conversion（店型轉換業務事件）**：評定為 **`BLOCKED_BY_EVIDENCE`**。
   - **Repo 內查證事實**：PostgreSQL（`infra/db/migrations/000001_baseline_canonical_schema.sql`）與 SQLite（`infra/db/migrations/000004_durable_product_domain.sql`）Schema 中均無任何門市店型改裝、擴充或縮減之事件歷程表（僅 `core.stores.store_format_code` / `stores.store_format_code` 靜態欄位）；`TargetFormatRegistry` 僅依門市坪數推薦新設店型（選店型，非轉店型）；`simulator.py` 缺乏轉換資本支出、停業過渡期營收損失與客群保留模型；經檢查 repo 內 production-path 與 migration 之 `conversion` 命中，均為房源轉候選點（Listing-to-Candidate conversion）或歷史證據等級遷移（Evidence Level conversion 註記，如 `000013_evidence_level_alignment.sql:3`），明確排除此類非店型語境後，repo 內無任何實體店型轉換之資料結構或計算邏輯。
   - **業務／營運端待證實事項（Evidence Request）**：實體營運中是否存在既有門市改裝轉型之歷史專案、營運作業規範（Playbook）或線下改造財務模型，尚待權威 Source Owner（Store Operations / Real Estate Expansion & Finance Lead）正式證實。在業務定義與資料表結構確立前，評定為 `BLOCKED_BY_EVIDENCE`。

依據 Remediation Plan 第 0 批處置規則，兩項皆**不得**以 fixture 或 placeholder 宣稱 `IMPLEMENTATION_READY`，亦不應在缺乏資料餵養的情況下排入第 6 批程式碼開發，而應以 `BLOCKED_BY_EVIDENCE` 記錄明確 Source Reference、Evidence Request、Owner、檢視週期與重啟條件。

---

## 2. 查證方法與防偽原則

本次查證遵循以下嚴格準則：
1. **嚴格分立 Repo 現狀與業務未知**：區分「Repo 內經代碼與 Schema 驗證未發現生產路徑」（客觀可重現事實）與「業務組織或外部廠商是否存在對應資料」（需向權威 Source Owner 發出 Evidence Request 查證之待定事項），不將 Repo 缺乏生產者等同於外部業務絕對不存在。
2. **拒絕子字串假陽性**：不以全樹 grep 搜尋「transfer」或「conversion」之命中數充當證據，逐一核對實體定義、資料表 DDL、dbt 轉換邏輯與 Python dataclass 欄位。
3. **拒絕假視圖／Mock 充當生產就緒**：嚴格區分「SQL view 語法存在」與「底層具備真實資料源」；硬編碼常數或 cross-join 生成之 baseline 視圖不得視為資料已準備就緒。
4. **逐層端到端追溯**：自來源端（Source Provider）→ 持久層（PostgreSQL Migration）→ dbt 就緒層（Model-Ready View）→ 領域層（Domain Feature Input）→ 消費 API 與決策輸出端逐層檢驗。

---

## 3. Member 1：Brand Transfer（既有品牌客群移轉）查證

### 3.1 需求定義與業務意涵
量化當目標商圈引入 ODayPlus 門市或競品門市異動時，消費者在既有品牌／姊妹品牌／競品品牌間的轉移比例與客群流動率（Cross-brand migration ratio），以修正預估需求。

### 3.2 來源參照（Source References）
- 規格與治理清單：`delivery_toolchain/governance/set_valued_requirements.json`（`ODP-FR-SITE-001`）
- 基準模型說明：`docs/data/MODEL_READY_VIEWS_BASELINE.md`（載明 `brand_transfer_view` 為 synthetic mock）
- dbt 模型定義：`pipelines/dbt/models/model_ready/brand_transfer_view.sql`
- 實體綱要：`infra/db/migrations/000001_baseline_canonical_schema.sql`、`000002_data_domain_canonical_entities.sql`、`000004_durable_product_domain.sql`
- 領域消費端：`modules/sitescore/domain/scoring.py`、`modules/sitescore/v3/application/service.py`

### 3.3 全鏈路追溯與查證事實

| 層級 | 檢查目標與路徑 | Repo 內查證事實（可重現技術現狀） | 業務／外部待證實事項（Evidence Request） | 判斷 |
|---|---|---|---|---|
| **來源生產者 (Producer)** | 外部消費面板 / POS 跨品牌交易日誌 / 會員跨店足跡 | Repo 內**無任何生產者程式碼或外部接入器**。 | 待 Commercial Strategy / Data Platform 確認是否有離線採購之市調面板、信用卡發票數據或會員跨品牌流轉資料庫。 | **Repo 未接入** |
| **持久層 (PostgreSQL)** | `core.brands` 表（`infra/db/migrations/000001_...` 等） | `core.brands` 僅有 `brand_id`, `brand_code`, `brand_name`, `brand_type`, `brand_capture_group`, `status` 等靜態主檔欄位，**無轉移機率或交易矩陣**。 | 待 Data Platform Lead 確認未來是否規劃 `core.brand_transfer_matrices` 或同等時序轉移綱要。 | **僅靜態主檔** |
| **dbt 就緒層 (Model-ready)** | `pipelines/dbt/models/model_ready/brand_transfer_view.sql` | `brand_transfer_view.sql` 以 `core.brands b1 CROSS JOIN core.brands b2` 笛卡兒積生成，硬編碼：<br>• `transfer_ratio = 0.15`<br>• `data_quality_score = 1.0`<br>• `confidence = 1.0`<br>• `store_format_code = 'ODAY_G2'`<br>文件明確註記為 safe, predictable mock baseline。 | 待確認正式 dbt 模型之真實上游源表與轉換公式規格。 | **合成 Mock** |
| **信號存儲 (Signal Store)** | `services/signal-store/client.py:319` | 僅在範例 mock payload 中出現 `"brand_transfer_confidence": 0.76`，無任何產品模組引用此 client。 | 待確認 Signal Store 是否有未入 repo 之獨立微服務實例與生產 topic。 | **假樣本** |
| **領域消費端 (SiteScore)** | `modules/sitescore/domain/scoring.py`<br>`modules/sitescore/v3/application/service.py` | `SiteScoreFeatureInput` 與評分計算邏輯完全沒有 `brand_transfer` 相關欄位或特徵輸入；評分模型從未消費 `brand_transfer_view`。 | 待演算法團隊提供特徵工程整合規範與消費介面設計。 | **未接入** |

### 3.4 待查證需求單（Evidence Request）
- **需求單編號**：`ER-SITE001-BRAND-TRANSFER-001`
- **對象（Source Owner）**：Commercial Strategy Lead / Data Platform (Market Intelligence Lead)
- **待確認事項**：
  1. 是否存在已簽約或規劃中之外部消費者跨品牌移轉數據源（例如發票載具數據、市調面板數據、會員跨店交易流）？
  2. 若存在，預計交付之資料綱要（Schema）、更新頻率（Freshness / SLA）及資料品質指標為何？
  3. 若短期內無資料源，是否同意以正式需求修訂（Amendment）或風險豁免（Waiver）將該成員自 `ODP-FR-SITE-001` 當前實作範疇排除？

### 3.5 獨立處置（Disposition Record）

```yaml
requirement_id: ODP-FR-SITE-001
member: BRAND_TRANSFER
disposition_status: BLOCKED_BY_EVIDENCE
source_owner: Commercial Strategy / Data Platform (Market Intelligence Lead)
evidence_request_id: ER-SITE001-BRAND-TRANSFER-001
source_references:
  - delivery_toolchain/governance/set_valued_requirements.json
  - docs/data/MODEL_READY_VIEWS_BASELINE.md
  - pipelines/dbt/models/model_ready/brand_transfer_view.sql
  - infra/db/migrations/000001_baseline_canonical_schema.sql
  - modules/sitescore/domain/scoring.py
schema_status: ABSENT_IN_REPO (core.brands contains static metadata only; no migration matrix in schema)
freshness: UNVERIFIED_NO_REPO_PRODUCER (no streaming or scheduled batch producer in repo)
sample_lineage: core.brands (metadata) -> brand_transfer_view (synthetic cross-join with fixed 0.15 ratio) -> unconsumed
next_review_date: 2026-10-01
reopen_trigger: >-
  External consumer panel feed (e.g. receipt/panel provider) or cross-brand POS loyalty dataset
  is formally contracted and ingested into raw data platform with versioned schema, verified producer SLA,
  and formal feature extraction specification approved by Commercial Strategy Lead.
```

---

## 4. Member 2：Format Conversion（店型轉換業務事件）查證

### 4.1 需求定義與業務意涵
針對營運中既有門市進行店型變更（例如：自一代店升級為 ODAY_G2 標準店、縮減為 G3 Compact、或擴充為 Flagship 旗艦店），計算其改裝資本支出（Remodeling Capex）、停業裝修期間之營收中斷損失、轉型後爬坡曲線（Ramp-up）及投資回收期（Payback）。

### 4.2 來源參照（Source References）
- 規格與治理清單：`delivery_toolchain/governance/set_valued_requirements.json`（`ODP-FR-SITE-001`）
- 實體綱要（PostgreSQL）：`infra/db/migrations/000001_baseline_canonical_schema.sql`（`core.stores`）
- 實體綱要（SQLite）：`infra/db/migrations/000004_durable_product_domain.sql`（`stores`）
- 非店型語境排除（Evidence Level 遷移）：`infra/db/migrations/000013_evidence_level_alignment.sql`
- 店型規格：`modules/site_economics/domain/formats.py`（`TargetFormatRegistry`）
- 財務模擬：`modules/site_economics/domain/simulator.py`（`SimulationInput`）
- 工作流設計：`docs/design/ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md`

### 4.3 全鏈路追溯與查證事實

| 層級 | 檢查目標與路徑 | Repo 內查證事實（可重現技術現狀） | 業務／營運端待證實事項（Evidence Request） | 判斷 |
|---|---|---|---|---|
| **業務事件 (Business Event)** | 門市改裝轉型專案 / 營運歷史紀錄 | Repo 內無任何門市改裝專案紀錄、轉型事件日誌或過渡期營運數據。 | 待 Store Operations 確認線下營運實務是否曾執行門市改裝轉型專案，或目前是否已有改裝轉型標準作業手冊（Playbook）。 | **Repo 無事件記錄** |
| **持久層 (PostgreSQL & SQLite)** | 1. `core.stores` 表（`infra/db/migrations/000001_baseline_canonical_schema.sql`）<br>2. `stores` 表（`infra/db/migrations/000004_durable_product_domain.sql`） | PostgreSQL 與 SQLite schema 均僅記錄門市當前靜態店型代碼（`store_format_code`），**無 `store_format_conversions`、`renovations` 或歷史變更履歷表**。 | 待 Data Platform / Store Operations 確認未來是否規劃店型異動歷史綱要與轉型歷程表。 | **僅靜態代碼** |
| **店型規格與註冊表** | `modules/site_economics/domain/formats.py:400-444` (`TargetFormatRegistry`) | `find_best_format_for_area(area_ping)` 僅依門市坪數推薦新設店型（<20 坪 G3_COMPACT，20-35 坪 G2，>35 坪 FLAGSHIP）。**此為新店店型選定（Selection），非現存店型轉換（Conversion）**。 | 待確認店型註冊表是否需要擴充轉換矩陣（例如 G1 轉 G2 之改裝費用與面積調整規則）。 | **新店選型非轉換** |
| **財務模擬引擎** | `modules/site_economics/domain/simulator.py:28-85` (`SimulationInput`) | 模擬引擎僅計算新設門市之設備支出、裝修費、標準爬坡與折現現金流。**完全無改裝停業損失、舊機殘值折抵、改裝額外成本等轉換計算路徑**。 | 待 Real Estate Expansion & Finance 提供 Brownfield 改裝之財務模擬模型公式與參數。 | **無轉換模型** |
| **非店型語境排除（Production-Path 與 Migration）** | 1. 房源流轉路徑：`apps/web/features/operator/NetworkFindAreasWorkspace.tsx:483`<br>`docs/design/ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md:159`<br>`docs/evidence/completion/ODP-OC-R4-005/`<br>2. 遷移腳本語境：`infra/db/migrations/000013_evidence_level_alignment.sql:3` | 經逐項查證已檢查之 production-path 與 migration 命中：<br>• 房源流程命中全為 **`Listing-to-Candidate conversion`（房源物件轉為評估候選點之流程狀態推進）**。<br>• `000013_evidence_level_alignment.sql:3` 之命中為 **`Evidence Level conversion`（歷史證據等級舊值無對應轉換之遷移註記）**。<br>明確排除上述非店型語境後，repo 內無任何店型轉換業務邏輯。 | 無（已明確排除 Listing 狀態推進與 Evidence Level 遷移之非店型語境假陽性）。 | **非店型語境排除** |

### 4.4 待查證需求單（Evidence Request）
- **需求單編號**：`ER-SITE001-FORMAT-CONVERSION-001`
- **對象（Source Owner）**：Store Operations / Real Estate Expansion & Finance (Site Economics Lead)
- **待確認事項**：
  1. 實體門市營運中是否具備既有店型改裝轉型（Brownfield Conversion）之業務流程、過渡期規範或歷史試點數據？
  2. 若有業務需求，改裝停業期營收折損、舊設備殘值處分、改裝 Capex 折舊與轉型後 Ramp 曲線之標準財務模型參數由誰提供？
  3. 若當前業務重心全為 Greenfield 新店展店而無改裝轉型需求，是否同意走正式需求修訂（Amendment）或風險豁免（Waiver）將該成員自 `ODP-FR-SITE-001` 暫時排除？

### 4.5 獨立處置（Disposition Record）

```yaml
requirement_id: ODP-FR-SITE-001
member: FORMAT_CONVERSION
disposition_status: BLOCKED_BY_EVIDENCE
source_owner: Store Operations / Real Estate Expansion & Finance (Site Economics Lead)
evidence_request_id: ER-SITE001-FORMAT-CONVERSION-001
source_references:
  - delivery_toolchain/governance/set_valued_requirements.json
  - infra/db/migrations/000001_baseline_canonical_schema.sql
  - infra/db/migrations/000004_durable_product_domain.sql
  - infra/db/migrations/000013_evidence_level_alignment.sql
  - modules/site_economics/domain/formats.py
  - modules/site_economics/domain/simulator.py
  - docs/design/ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md
schema_status: ABSENT_IN_REPO (no format conversion event or renovation history table in PostgreSQL 000001 or SQLite 000004 schemas)
freshness: UNVERIFIED_NO_REPO_PRODUCER (no operational event stream or conversion logging in repo)
sample_lineage: TargetFormatRegistry (static area mapping) -> simulator.py (greenfield only) -> zero conversion paths
next_review_date: 2026-10-01
reopen_trigger: >-
  Store Operations formally approves a brownfield format conversion playbook with remodeling cost
  schedules and downtime impact parameters; schema migration introduces store format transition event tables
  and tracking with authoritative owner sign-off.
```

---

## 5. 對治理清單與 Remediation Plan 之影響

1. **`set_valued_requirements.json`**：
   - `ODP-FR-SITE-001` 中的 `BRAND_TRANSFER` 與 `FORMAT_CONVERSION` 保持 `absent` 狀態，其註記與本報告結論一致。
2. **Remediation Plan（第 0 批 → 第 6 批處置）**：
   - 依據計畫規則：「若答案是資料不存在或尚無生產者，這兩項走正式需求修訂或具期限的 waiver／risk acceptance，先做才不會排到假工作」。
   - **結論**：第 6 批實作清單中，`SITE-001 Brand Transfer` 與 `SITE-001 Format Conversion` **不進入實作開發**，避免建立裝飾性功能或偽造假模型，直至滿足上述 Reopen Trigger 並由權威 Source Owner 回覆 Evidence Request 為止。

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

# 4. 查證 PostgreSQL (000001) 與 SQLite (000004) schema 僅存靜態 store_format_code，無 conversion 歷程表
grep -n "store_format_code" infra/db/migrations/000001_baseline_canonical_schema.sql infra/db/migrations/000004_durable_product_domain.sql

# 5. 查證 000013 migration 為 Evidence Level conversion 註記而非店型轉換
grep -n -C 3 "conversion" infra/db/migrations/000013_evidence_level_alignment.sql
```
