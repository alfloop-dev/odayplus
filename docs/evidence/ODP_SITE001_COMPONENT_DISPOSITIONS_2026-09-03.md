# ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001 — SITE-001 Brand Transfer 與 Format Conversion 處置與 Human-Authority Handback 報告

- **任務識別碼**：`ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001`
- **文件路徑**：`docs/evidence/ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md`
- **日期**：2026-09-03
- **任務負責人**：Antigravity5（Helper Execution Lease: Antigravity3）
- **審查人**：Codex2
- **基準代碼**：`origin/dev` @ `e56eda40`
- **關聯需求**：`ODP-FR-SITE-001`（SiteScore 需求組成因子）
- **前置任務**：
  - `ODP-SITE001-DATA-READINESS-001`（查證 SITE-001 Brand Transfer 與 Format Conversion 的資料與業務事件）
  - `ODP-REQ-DISPOSITION-GOVERNANCE-001`（建立 MUST requirement amendment／waiver 的可機讀 disposition gate）
- **依據與來源**：
  - [修正計畫](../plans/ODP_REMEDIATION_PLAN_2026-09-03.md) §第 0 批 & 第 6 批
  - [待裁決事項](../plans/ODP_OPEN_DECISIONS_2026-09-03.md) §第 11 項
  - [結構性成因處理結果](ODP_STRUCTURAL_REMEDIATION_2026-09-01.md)
  - [SITE-001 資料準備度查證報告](ODP_SITE001_DATA_READINESS_2026-09-03.md)
  - [需求處置與治理政策](../governance/ODP_REQUIREMENT_DISPOSITIONS.md)
  - [集合型需求治理清單](../../delivery_toolchain/governance/set_valued_requirements.json)

---

## 1. 執行摘要與處置核心判定

本報告依據 `ODP-SITE001-DATA-READINESS-001` 之資料準備度查證事實，針對 `ODP-FR-SITE-001`（「系統必須組合 External Demand、Brand Transfer、Format Conversion、Ramp、Seasonality」）中尚未滿足的兩個成員 —— **Brand Transfer（既有品牌客群移轉）** 與 **Format Conversion（店型轉換業務事件）** 進行正式處置與生命週期狀態判定，並依據 Remediation Plan 規則建立需人類治理授權的 Handback Package。

### 1.1 核心處置結論

依據治理政策與防偽原則，本任務逐 member 判定獨立處置結果：

```
+-------------------------------------------------------------------------------------------------------+
|                                    ODP-FR-SITE-001 處置架構總覽                                       |
+----------------------+--------------------+-----------------------+-----------------------------------+
| 需求成員 (Member)    | 履約現況 (Status)  | 處置狀態 (Disposition) | 後續路徑 (Action Path)            |
+----------------------+--------------------+-----------------------+-----------------------------------+
| EXTERNAL_DEMAND      | satisfied          | VERIFIED              | 生產模型運算中 (SiteScore)        |
| RAMP                 | satisfied          | VERIFIED              | 生產模型運算中 (SiteScore)        |
| SEASONALITY          | satisfied          | VERIFIED              | 生產模型運算中 (SiteScore)        |
| BRAND_TRANSFER       | absent             | BLOCKED_BY_EVIDENCE   | 移交 HB-SITE001-BRAND-TRANSFER-001|
| FORMAT_CONVERSION    | absent             | BLOCKED_BY_EVIDENCE   | 移交 HB-SITE001-FORMAT-CONVERSION |
+----------------------+--------------------+-----------------------+-----------------------------------+
```

1. **Brand Transfer（既有品牌客群移轉）**：獨立判定為 **`BLOCKED_BY_EVIDENCE`**。
   - **判定依據**：Repo 內僅有 `core.brands` 靜態代碼主檔；`brand_transfer_view.sql` 僅為基於笛卡兒積（CROSS JOIN）的合成假視圖，硬編碼 `transfer_ratio = 0.15` 與滿分信心值；SiteScore 評分端亦未接入任何轉移欄位。Repo 內無任何真實生產者、Schema 綱要或消費路徑。
   - **實作拒絕**：未達 `IMPLEMENTATION_READY`。嚴禁將合成 Mock 視圖或固定常數接進生產評分模型，避免製造裝飾性限制與假精度。
   - **移交處置**：建立結構化 Human-Authority Handback 單（`HB-SITE001-BRAND-TRANSFER-001`），提報至 `Human/Ops`、`Architecture Board` 與 `Commercial Strategy Lead`，指定下次檢視日期為 **`2026-10-01`**。

2. **Format Conversion（店型轉換業務事件）**：獨立判定為 **`BLOCKED_BY_EVIDENCE`**。
   - **判定依據**：PostgreSQL（`000001`）與 SQLite（`000004`）Schema 均僅記錄靜態 `store_format_code`，無門市改裝或轉型歷程表；`TargetFormatRegistry` 僅依坪數挑選新設店型（選型而非轉型）；`simulator.py` 僅模擬 Greenfield 新店經濟效益，無 Brownfield 停業損失、舊機殘值折抵、改裝額外 Capex 與轉型 Ramp 曲線；經查證已明確排除房源狀態流轉（Listing-to-Candidate conversion）與證據等級遷移註記（000013）等非店型語境假陽性。
   - **實作拒絕**：未達 `IMPLEMENTATION_READY`。嚴禁在缺乏業務規範與財務模型的情況下自行於模擬器編造轉型折減邏輯。
   - **移交處置**：建立結構化 Human-Authority Handback 單（`HB-SITE001-FORMAT-CONVERSION-001`），提報至 `Human/Ops`、`Architecture Board` 與 `Retail Operations Lead`，指定下次檢視日期為 **`2026-10-01`**。

### 1.2 嚴格遵守治理防線（No AI Self-Signed Waivers）

- **AI 禁止自簽豁免**：AI 代理人嚴格遵守 `docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md` 規範，絕不將未滿足成員逕自標記為 `DECIDED`，亦不假造人類簽署人。
- **維持客觀技術現況**：在 `set_valued_requirements.json` 中維持 `status: "absent"` 與 `disposition.state: "BLOCKED_BY_EVIDENCE"`，完整記錄 `evidence_needed`、`evidence_owner`、`next_review_date`、`rationale` 與 `formal_handback_ref`。

---

## 2. Member 1：Brand Transfer（既有品牌客群移轉）處置

### 2.1 需求定義與資料準備度查證摘要

`ODP-FR-SITE-001` 之 `BRAND_TRANSFER` 需求旨在量化目標商圈引入門市時，消費者在既有品牌／競品品牌間的轉移流動比例（Cross-brand migration ratio）。

依據 `ODP_SITE001_DATA_READINESS_2026-09-03.md` 查證事實：
- **來源生產者 (Producer)**：Repo 內無任何外部消費面板接入器、POS 跨品牌日誌流或批次排程。
- **持久層 (PostgreSQL)**：`core.brands` 僅包含 `brand_id`, `brand_code`, `brand_name`, `brand_type`, `brand_capture_group`, `status` 等靜態主檔欄位，無時序轉移矩陣表。
- **dbt 就緒層 (Model-Ready)**：`pipelines/dbt/models/model_ready/brand_transfer_view.sql` 以 `core.brands b1 CROSS JOIN core.brands b2` 笛卡兒積生成，硬編碼 `transfer_ratio = 0.15`、`data_quality_score = 1.0`、`confidence = 1.0`，且 `docs/data/MODEL_READY_VIEWS_BASELINE.md` 明確註記為 synthetic mock baseline。
- **信號存儲 (Signal Store)**：`services/signal-store/client.py:319` 僅於 mock payload 中包含 `"brand_transfer_confidence": 0.76`，無任何生產模組引用。
- **領域消費端 (SiteScore)**：`modules/sitescore/domain/scoring.py` 與 `modules/sitescore/v3/application/service.py` 均無任何品牌轉移欄位，從未消費 `brand_transfer_view`。

### 2.2 為何拒絕接入生產消費端（Refusal of Synthetic Wiring）

若為了滿足需求形式而將現有 `brand_transfer_view.sql` 接進 `modules/sitescore`：
1. **注入裝飾性常數**：笛卡兒積產生的 `0.15` 轉移率與 `1.0` 滿分信心值將無差別注入所有拓店候選點評估中。
2. **違反 anti-measurement-defaults 政策**：未經真實量測的合成數據被標記為滿分品質與滿分信心，實質掩蓋了資料缺失的事實，誤導投資決策。
3. **破壞模型可解釋性**：評估報告宣稱考慮了「品牌客群轉移」，實則為固定常數疊加，製造假精度。

因此，本任務**拒絕撰寫任何將 mock brand transfer 接入 SiteScore 生產路徑的程式碼**。

### 2.3 人類授權移交單（Human-Authority Handback Package）

```yaml
handback_id: HB-SITE001-BRAND-TRANSFER-001
requirement_id: ODP-FR-SITE-001
member: BRAND_TRANSFER
current_disposition_state: BLOCKED_BY_EVIDENCE
evidence_request_ref: docs/evidence/ODP_SITE001_DATA_READINESS_2026-09-03.md#34-待查證需求單evidence-request
designated_authority:
  - Commercial Strategy Lead (Market Intelligence)
  - Platform Architecture Board
  - Human/Ops
assigned_risk_owner: Market Intelligence Lead
next_review_date: 2026-10-01
reopen_trigger: >-
  External consumer panel feed (e.g. receipt/panel provider) or cross-brand POS loyalty dataset
  is formally contracted and ingested into raw data platform with versioned schema, verified producer SLA,
  and formal feature extraction specification approved by Commercial Strategy Lead.

decision_pathways:
  pathway_a_implementation:
    description: "採購/接入真實品牌轉移資料源並實作生產模型"
    prerequisites:
      - "完成外部資料源採購或跨品牌會員交易日誌接入"
      - "建立 raw ingestion pipeline 與持久層遷移（如 core.brand_transfer_matrices）"
      - "重寫 pipelines/dbt/models/model_ready/brand_transfer_view.sql，移除 CROSS JOIN 與固定常數"
      - "於 modules/sitescore 定義 BrandTransferFeatureInput 並實作特徵整合"
      - "建立真實數據與缺失數據的反事實測試（Counterfactual Test）"
    target_state: "IMPLEMENTATION_READY"

  pathway_b_formal_amendment_or_waiver:
    description: "由人類授權人簽署正式需求修訂 (Amendment) 或具期限豁免 (Waiver)"
    prerequisites:
      - "人類治理角色（Human/Ops / Architecture Board / Commercial Strategy Lead）簽署"
      - "提供 6 大法定欄位：formal_decision_ref, decider, scope, risk_owner, expiry, reopen_trigger"
      - "註冊於 docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md"
    target_state: "DECIDED"
```

---

## 3. Member 2：Format Conversion（店型轉換業務事件）處置

### 3.1 需求定義與資料準備度查證摘要

`ODP-FR-SITE-001` 之 `FORMAT_CONVERSION` 需求旨在針對既有門市進行店型變更（如一代店升級為 ODAY_G2 標準店、縮減為 G3 Compact、或擴充為 Flagship 旗艦店），計算其改裝資本支出（Remodeling Capex）、停業裝修期間之營收損失、轉型後爬坡曲線（Ramp-up）及投資回收期。

依據 `ODP_SITE001_DATA_READINESS_2026-09-03.md` 查證事實：
- **業務事件 (Business Event)**：Repo 內無任何門市改裝專案紀錄、轉型事件日誌或過渡期營運數據。
- **持久層 (PostgreSQL & SQLite)**：PostgreSQL `core.stores`（`000001`）與 SQLite `stores`（`000004`）Schema 均僅記錄當前靜態店型代碼 `store_format_code`，無 `store_format_conversions` 或歷史變更履歷表。
- **店型規格與註冊表**：`modules/site_economics/domain/formats.py` 之 `TargetFormatRegistry.find_best_format_for_area()` 僅依坪數推薦新設店型（新店選型，非既有門市轉型）。
- **財務模擬引擎**：`modules/site_economics/domain/simulator.py` 僅計算新設門市之 Greenfield 資本支出與現金流，無改裝停業損失、舊機殘值折抵或改裝額外成本等轉換計算路徑。
- **非店型語境排除**：明確排除房源狀態流轉（Listing-to-Candidate conversion）與歷史證據等級遷移註記（000013_evidence_level_alignment.sql）等非店型轉換語境。

### 3.2 為何拒絕接入生產消費端（Refusal of Dummy Conversion Logic）

若在缺乏真實業務作業手冊與財務參數的前提下：
1. **捏造改裝扣減公式**：在 `simulator.py` 中自行撰寫假設性的改裝折減公式，屬於製造無實務依據的假模型。
2. **扭曲既有新店評估**：既有拓店評估為 Greenfield 新設門市，若強行引入未定義的轉換邏輯，可能導致新店與舊店改造的評估邊界混淆。

因此，本任務**拒絕在缺少業務規格前撰寫任何假店型轉換計算程式**。

### 3.3 人類授權移交單（Human-Authority Handback Package）

```yaml
handback_id: HB-SITE001-FORMAT-CONVERSION-001
requirement_id: ODP-FR-SITE-001
member: FORMAT_CONVERSION
current_disposition_state: BLOCKED_BY_EVIDENCE
evidence_request_ref: docs/evidence/ODP_SITE001_DATA_READINESS_2026-09-03.md#44-待查證需求單evidence-request
designated_authority:
  - Store Operations Lead
  - Real Estate Expansion & Finance Lead (Site Economics)
  - Platform Architecture Board
  - Human/Ops
assigned_risk_owner: Retail Operations Lead / Site Economics Lead
next_review_date: 2026-10-01
reopen_trigger: >-
  Store Operations formally approves a brownfield format conversion playbook with remodeling cost
  schedules and downtime impact parameters; schema migration introduces store format transition event tables
  and tracking with authoritative owner sign-off.

decision_pathways:
  pathway_a_implementation:
    description: "門市營運端確立改裝轉型標準作業與財務模型後實作"
    prerequisites:
      - "門市營運團隊發布 Brownfield Format Conversion Playbook"
      - "財務團隊提供改裝停業損失、設備汰換殘值與改裝 Capex 折舊參數"
      - "資料庫新增 core.store_format_conversions 轉型履歷表（含 migration 與 SQLite 對齊）"
      - "擴充 modules/site_economics/domain/simulator.py 支援 ConversionSimulationInput"
      - "建立改裝門市與新設門市的反事實比對測試"
    target_state: "IMPLEMENTATION_READY"

  pathway_b_formal_amendment_or_waiver:
    description: "由人類授權人簽署正式需求修訂 (Amendment) 或具期限豁免 (Waiver)"
    prerequisites:
      - "人類治理角色（Human/Ops / Architecture Board / Store Operations Lead）簽署"
      - "提供 6 大法定欄位：formal_decision_ref, decider, scope, risk_owner, expiry, reopen_trigger"
      - "註冊於 docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md"
    target_state: "DECIDED"
```

---

## 4. 下游達標啟用時的設計契約與反事實驗收標準

當 Human/Ops 或權威業務負責人回應 Handback Package 並解除 `BLOCKED_BY_EVIDENCE` 後，未來承接實作之任務必須遵循以下設計契約與驗收標準：

### 4.1 Brand Transfer 實作契約與驗收標準（Future Implementation Spec）

1. **資料流與視圖契約**：
   - 上游源表：`core.brand_transfer_matrices`（由真實會員跨店或外部消費面板 ETL 寫入）。
   - dbt 視圖：`pipelines/dbt/models/model_ready/brand_transfer_view.sql` 必須依據真實觀測計算 `transfer_ratio`，並根據樣本覆蓋率計算真實 `data_quality_score` 與 `confidence`（禁止固定 1.0）。
2. **消費端接入契約**：
   - `modules/sitescore/domain/scoring.py` 新增 `brand_transfer_ratio: float | None = None`。
   - 消費端邏輯遵循 fail-closed 棄權原則：若商圈內存在多品牌但缺乏移轉數據，走 feasibility rules 標記或降級處理，禁止預設滿分。
3. **反事實驗收測試（Counterfactual Acceptance Criteria）**：
   - **測試 1（正向靈敏度）**：在其他條件完全相同下，目標門市若由高品牌移轉率商圈切換至零移轉率商圈，預估營收與評分必須產生統計顯著之差異。
   - **測試 2（缺值防禦）**：當 `brand_transfer_ratio` 為 `None` 時，評分模型不得產出與完整資料同等的確定性分數，且報告中必須明確標示 `unmodelled_brand_transfer` 或相應降級標籤。

### 4.2 Format Conversion 實作契約與驗收標準（Future Implementation Spec）

1. **持久層實體契約**：
   - Migration `core.store_format_conversions` 記錄：
     - `conversion_id UUID PRIMARY KEY`
     - `store_id UUID REFERENCES core.stores(store_id)`
     - `from_format_code VARCHAR(50) NOT NULL`
     - `to_format_code VARCHAR(50) NOT NULL`
     - `remodeling_capex NUMERIC(12,2) NOT NULL`
     - `downtime_days INTEGER NOT NULL`
     - `conversion_start_date DATE NOT NULL`
     - `conversion_completed_date DATE`
2. **模擬引擎契約**：
   - `modules/site_economics/domain/simulator.py` 新增 `ConversionSimulationInput`，納入停業期營收折損（$DowntimeLoss = DailyBaseline \times DowntimeDays$）及舊設備殘值折抵。
3. **反事實驗收測試（Counterfactual Acceptance Criteria）**：
   - **測試 1（停業折損驗證）**：相同坪數與目標店型下，Brownfield 改裝專案之第一年淨現金流必須精確反映停業天數造成的營收損失，且投資回收期必須長於零停業期之對照組。
   - **測試 2（殘值折抵驗證）**：舊設備殘值折抵金額提高時，專案初期淨資本支出必須等額下降。

---

## 5. 治理清單與規範對齊狀態

1. **`delivery_toolchain/governance/set_valued_requirements.json`**：
   - `ODP-FR-SITE-001` 成員 `BRAND_TRANSFER` 與 `FORMAT_CONVERSION` 保持 `absent` 狀態，其 `disposition` 區塊宣告 `state: "BLOCKED_BY_EVIDENCE"`，並附帶完整法定追蹤欄位與本報告之 handback 參照。
2. **`docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md`**：
   - §4.2 登錄正式處置記錄、證據請求單號、Handback Package 編號、風險負責人與下次檢視時點。
3. **自動化驗證**：
   - `delivery_toolchain/governance/check_requirement_members.py` 檢查維持全綠燈通過。

---

## 6. 可重現驗證收據（Reproducibility Receipts）

以下驗證指令於本分支工作目錄執行全數通過：

```bash
# 1. 驗證集合型需求治理清單與處置檢查器全數通過
uv run --python 3.12 python delivery_toolchain/governance/check_requirement_members.py --show-dispositions --show-gaps

# 2. 執行治理檢查器單元與整合測試套件 (41 passed)
uv run --python 3.12 pytest delivery_toolchain/governance/test_check_requirement_members.py

# 3. 查證 brand_transfer_view 仍為 mock baseline，未被非法接入生產評分
grep -n -C 3 "transfer_ratio" pipelines/dbt/models/model_ready/brand_transfer_view.sql

# 4. 查證 TargetFormatRegistry 僅作坪數選型，未包含假改裝轉換邏輯
python3 -c "from modules.site_economics.domain.formats import DEFAULT_FORMAT_REGISTRY; print(DEFAULT_FORMAT_REGISTRY.list_codes())"
```
