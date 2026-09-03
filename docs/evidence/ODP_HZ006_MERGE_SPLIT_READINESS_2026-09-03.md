# ODP-HZ006-MERGE-SPLIT-READINESS-001 — 熱區合併／拆分建模門檻與實績判定

- **任務識別碼**：`ODP-HZ006-MERGE-SPLIT-READINESS-001`
- **文件路徑**：`docs/evidence/ODP_HZ006_MERGE_SPLIT_READINESS_2026-09-03.md`
- **日期**：2026-09-03
- **任務負責人**：Claude2（Helper Lease: Antigravity5）
- **審查人**：Antigravity4
- **基準代碼**：`origin/dev` @ `8479567d`
- **判定狀態**：`BLOCKED_BY_EVIDENCE`（實績未達建模門檻，阻擋至 Human/Ops）
- **下次檢查時點（Next-Check Date）**：`2026-12-01`（第一階段 3 個月回顧）／`2027-03-01`（完整 6 個月門檻期）
- **關聯依據**：
  - `docs/plans/ODP_REMEDIATION_PLAN_2026-09-03.md` §第 6 批（`HZ-006` 項目）
  - `docs/plans/ODP_OPEN_DECISIONS_2026-09-03.md` §第 13 項（`HZ-006`）
  - `docs/evidence/ODP_STRUCTURAL_REMEDIATION_2026-09-01.md`
  - `docs/design/ODP-SA-06-AMD-001.md` §3.2（`ODP-FR-HZ-006`）、§3.5（`ODP-FR-HZ-004`）
  - `docs/design/ODP-SD-AMD-001.md` §5.1（需求吸收閉環）、§5.2（熱區組成與 `expansion.heatzone_composition`）

---

## 1. 任務核心結論與判定

依據修正計畫（`ODP_REMEDIATION_PLAN_2026-09-03.md`）與待裁決事項（`ODP_OPEN_DECISIONS_2026-09-03.md`）的架構原則：**禁止憑純空間幾何或結構假設盲目實作熱區合併／拆分（merge/split）演算法**。空間單元合併或拆分本質上是將相鄰 H3 網格之需求吸收與客群流動視為單一連續商圈或異質子商圈，其決策必須建立在 `ODP-FR-HZ-004` 實際門市營運實績（realised revenue & demand absorption）閉環累積的數據之上。

### 核心查證事實

1. **實績累積現況**：查驗當前 PG16 生產模型快照（`pg16-production-model-inventory-2026-07-25-v1`，SHA-256 `3f1c8ec4baa1e2f06f5c4e93e82a6258315012b46aacfd3f3e578221aa8b5f44`）與 Gate 1 收據（`GATE1_BENCHMARK_RECEIPT.json`），`model_ready.heatzone_training_view`（契約 `heatzone-training-view-v2`）之合格成熟實績標籤數為 **0**（低於門檻 200），狀態為 `FAIL_CLOSED`（`DATA_CONTRACT_NOT_MATURE`）。
2. **吸收機制剛就緒**：`ODP-FR-HZ-004` 需求吸收管線（`modules/heatzone/v3/absorption.py`、`modules/heatzone/application/absorption_inputs.py`）於近期合併（PR #1110 / #1142），生產環境尚未累積多月份跨網格之門市吸收實績歷史。
3. **裁決處置**：判定本任務進入 **`BLOCKED_BY_EVIDENCE`**，正式提報阻擋至 **`Human/Ops`**，並明定下次檢查時點為 **`2026-12-01`**。
4. **防禦性實作邊界**：本任務**嚴禁在此時撰寫任何投機性 merge/split 空間聚合演算法**（如基於靜態距離的 greedy k-ring 合併或硬編 threshold 拆分），避免製造無實績依據的假商圈結構；本文件完整定義四大維度量化門檻、反事實驗收與可逆回滾契約，待實績達標後供下游實作任務直接承接。

---

## 2. 生產快照與 HZ-004 實績來源查驗

本判定僅採用正式生產環境與持久層快照標識，拒絕任何 fixture、mock、synthetic 或 auto-seeded 數據：

| 項目 | 查驗座標／值 | 狀態／查證結果 |
|---|---|---|
| **Inventory Version** | `pg16-production-model-inventory-2026-07-25-v1` | 穩定基準快照 |
| **Inventory Observed At** | `2026-07-25T15:20:00Z` | 正式觀測時點 |
| **Inventory SHA-256** | `3f1c8ec4baa1e2f06f5c4e93e82a6258315012b46aacfd3f3e578221aa8b5f44` | 不可變摘要已驗證 |
| **Target View & Contract** | `model_ready.heatzone_training_view` (`heatzone-training-view-v2`) | 關聯視圖就緒 |
| **Observed Real Labels** | `0` | 實績為 0（門檻 ≥ 200） |
| **Eligible Mature Labels** | `0` | 實績為 0（門檻 ≥ 200） |
| **Gate 1 Benchmark Receipt** | `docs/evidence/models/ODP-PLAN-HEATZONE-OUTCOME-001/GATE1_BENCHMARK_RECEIPT.json` | `verdict: FAIL_CLOSED` |
| **Production Binding Status** | `governed_disabled = true` (`DATA_CONTRACT_NOT_MATURE`) | 符合平台安全政策 |
| **HZ-004 Code Artifacts** | `modules/heatzone/v3/absorption.py`、`modules/heatzone/application/absorption_inputs.py` | 吸收契約已就緒，待實績累積 |
| **Database Persistence** | `expansion.heatzone_scores`（含吸收四欄約束及 `chk_heatzone_absorption_source`） | PostgreSQL/SQLite 已對齊 |

---

## 3. 四大維度建模與啟用門檻（Readiness Thresholds）

為防止「無實績支持的空間幾何假合併」，下游任務啟動 `ODP-FR-HZ-006` merge/split 演算法與模型實作前，必須完全滿足下列四大維度的量化門檻：

```
+-----------------------------------------------------------------------------------+
|              HeatZone Merge/Split 建模準備度四大門檻 (Readiness Gates)              |
+-------------------------+-------------------------+-------------------------------+
|  1. 最小月份 (Horizon)   |  2. 樣本量 (Sample Size) |  3. 區域覆蓋 (Geo Coverage)    |
|  - 連續 >= 6 個月實績     |  - >= 200 筆成熟標籤    |  - >= 2 大都會商圈聚落        |
|  - 排除 Ramp 觀察期     |  - >= 50 家成熟營業門市 |  - >= 80% 連續 H3 空間覆蓋    |
|  - 100% 無缺口日營收    |  - >= 30 組相鄰網格對   |  - 網格密度 >= 3 實體零售點   |
+-------------------------+-------------------------+-------------------------------+
|                             4. 穩定性與漂移 (Stability & Drift)                    |
|  - 60 日滾動吸收率變異係數 CV < 0.15 (15%)                                        |
|  - 人口活力與競爭壓力特徵漂移 PSI < 0.10、Wasserstein < 0.05                        |
|  - 剩餘需求與 SiteScore (ODP-FR-SITE-003) 稀釋指標算術完全一致                    |
+-----------------------------------------------------------------------------------+
```

### 3.1 最小月份／時間長度門檻（Observation Horizon）

1. **最小觀測期**：候選空間商圈聚落必須累積至少 **連續 6 個日曆月（≥ 180 個連續營業日）** 的已實現門市營收記錄（`StoreDailyPerformance`）。
2. **嚴格排除開店爬坡期（Ramp Period Exclusion）**：
   - 門市自 `observed_start_business_date` 起算，位於 `DecisionPolicy.min_observation_days`（預設 30–90 天，依治理政策動態解析）之內的天數一律排除於吸收計算之外。
   - 只有爬坡期結束後的穩定營業日方得記入商圈需求吸收量。
3. **營業日完整性與零補值（Zero-Gap Complete Coverage）**：
   - 觀測視窗內之每日營收必須滿足 `coverage_state = complete` 且 `is_complete = True`。
   - 嚴禁分頁遺漏、缺日假歸零、或跨日平均補值；任何存在日資料缺口的門市一律 fail-closed 拒絕，不計入有效吸收期。

### 3.2 樣本量與門市規模門檻（Sample Size & Volume）

1. **成熟標籤規模**：`model_ready.heatzone_training_view` 累積之非合成（`is_synthetic = False`）、成熟合格實績標籤數 **≥ 200 筆**。
2. **活躍吸收門市量**：跨評估網格中，通過爬坡期且具備有效正營收之營業門市數（`absorbing_store_count`）**≥ 50 家**。
3. **候選相鄰網格對（Candidate Adjacent Pairs）**：至少有 **≥ 30 組** 相鄰 H3 網格同時具備跨網格門市營業實績與消費者活動軌跡，足以支撐網格間需求交互吸收之統計檢定。

### 3.3 區域與空間聚落覆蓋門檻（Geographic & Cluster Coverage）

1. **核心都會聚落覆蓋**：實績數據必須涵蓋至少 **2 個主要都會市場**（如雙北都會區、台中都會區、高雄都會區），避免單一特殊商圈（如特定夜市或單一車站）的局部效應過度擬合。
2. **空間連續性（Spatial Contiguity）**：在目標評估商圈半徑（k-ring = 1~2）內，H3 resolution 8/9 網格的實績觀測覆蓋率必須 **≥ 80%**，不得存在跳躍式的大面積數據真空。
3. **商圈實體密度**：評估網格聚落內必須具備 **≥ 3 個獨立零售／競品實體節點**，確保能觀測空間交叉彈性與商圈邊界效應。

### 3.4 穩定性與漂移約束（Stability & Drift Constraints）

1. **吸收率穩定性（Absorption Ratio Stability）**：
   - 候選合併網格在爬坡期後的滾動 60 天吸收比例（`absorption_ratio`）變異係數（$CV = \sigma / \mu$）必須 **$< 0.15$（15%）**。
   - 確保空間需求吸收係數反映的是結構性商圈承載力，而非短期促銷或季節性異常波動。
2. **特徵與預測漂移（Feature & Prediction Drift）**：
   - 依據 `model_performance_drift` 治理政策，候選聚落之人口活力、競爭壓力與租金分佈的總體穩定度指標（PSI）必須 **$< 0.10$**，Wasserstein 距離 **$< 0.05$**。
3. **跨模組一致性（Cross-Domain Consistency）**：
   - 熱區剩餘需求 `remaining_demand` 與 SiteScore 門市稀釋指標（`ODP-FR-SITE-003`）必須直接共用 `compute_absorbed_demand()` 輸出，禁止兩模組各自衍生不一致的吸收量。

---

## 4. 當前準備度比對與處置裁決

### 4.1 門檻達標比對表

| 門檻維度 | 指標項目 | 規範要求門檻 | 當前生產快照實績 | 判定結果 |
|---|---|---|---|:---:|
| **時間長度** | 爬坡期後連續實績月份 | ≥ 6 個月（≥ 180 日） | 0 個月 | ❌ 未達標 |
| **樣本量** | 合格成熟真實標籤數 | ≥ 200 筆 | 0 筆 | ❌ 未達標 |
| **門市規模** | 參與吸收計算之活躍門市數 | ≥ 50 家 | 0 家 | ❌ 未達標 |
| **網格對數** | 具實績之相鄰候選網格對 | ≥ 30 組 | 0 組 | ❌ 未達標 |
| **區域覆蓋** | 都會核心聚落覆蓋數 | ≥ 2 個都會區 | 0 個 | ❌ 未達標 |
| **穩定性** | 60日滾動吸收變異係數 $CV$ | < 0.15 | 無資料（N/A） | ❌ 未達標 |
| **漂移控制** | 空間特徵 PSI / Wasserstein | PSI < 0.10, W < 0.05 | 無資料（N/A） | ❌ 未達標 |

### 4.2 任務處置與 Blocker 登記

- **狀態處置**：判定本任務為 **`BLOCKED_BY_EVIDENCE`**。
- **Blocker 對象**：**`Human/Ops`**（數據營運與 POS 數據接入團隊）。
- **Blocker 訊息**：
  > `BLOCKED_BY_EVIDENCE: HZ-006 heat-zone merge/split modeling requires >=6 months and >=200 mature HZ-004 retained absorption labels. Current pg16 inventory has 0 mature labels (DATA_CONTRACT_NOT_MATURE). No merge/split heuristics written per safety rules. Recheck scheduled on 2026-12-01.`
- **下次檢查時點（Next-Check Date）**：
  - **`2026-12-01`**（Q4 季度檢查點：檢核首波 3 個月實績累積與日營收資料品質）。
  - **`2027-03-01`**（目標達標檢查點：檢核完整 6 個月門檻與 Gate 1 標籤就緒度）。

---

## 5. 達標時的實作契約、反事實驗收與回滾規範

當 Human/Ops 提供之真實營運實績累積達標並解鎖 Blocker 後，下游承接之實作任務（如 `ODP-HZ006-MERGE-SPLIT-IMPL-001`）必須直接遵循下列契約架構與反事實驗收規範：

### 5.1 資料模型與不可變持久層（`expansion.heatzone_composition`）

依據系統設計修正案 `ODP-SD-AMD-001` §5.2，合併與拆分歷程必須落庫於 `expansion.heatzone_composition`（Migration `000015_heatzone_composition.sql`）：

```sql
CREATE TABLE IF NOT EXISTS expansion.heatzone_composition (
    composition_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    zone_id             VARCHAR(100) NOT NULL,   -- 合併後熱區識別碼，格式 'MZ-{hash16}'
    tenant_id           UUID NOT NULL REFERENCES core.tenants(tenant_id),
    member_cell_id      UUID NOT NULL REFERENCES geo.h3_cells(geo_cell_id),
    composition_kind    VARCHAR(50) NOT NULL,    -- 'MERGED', 'SPLIT_CHILD', 'ATOMIC'
    parent_zone_id      VARCHAR(100),            -- SPLIT_CHILD 時指向原熱區
    decided_by          VARCHAR(255) NOT NULL,   -- 'system' 或操作者帳號
    decided_at          TIMESTAMP WITH TIME ZONE NOT NULL,
    decision_policy_version_id VARCHAR(100) NOT NULL,
    override_reason     TEXT,                    -- 人工推翻時必填
    reverted_at         TIMESTAMP WITH TIME ZONE,-- 撤銷時點；NULL = 生效中
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_composition_kind CHECK (
        composition_kind IN ('MERGED', 'SPLIT_CHILD', 'ATOMIC')
    ),
    CONSTRAINT chk_composition_parent CHECK (
        (composition_kind =  'SPLIT_CHILD' AND parent_zone_id IS NOT NULL)
     OR (composition_kind <> 'SPLIT_CHILD' AND parent_zone_id IS NULL)
    ),
    CONSTRAINT chk_composition_override_reason CHECK (
        (decided_by =  'system' AND override_reason IS NULL)
     OR (decided_by <> 'system' AND override_reason IS NOT NULL AND override_reason <> '')
    ),
    CONSTRAINT chk_composition_revert_order CHECK (
        reverted_at IS NULL OR reverted_at >= decided_at
    ),
    CONSTRAINT chk_composition_zone_id_format CHECK (zone_id ~ '^MZ-[0-9a-f]{16}$'),
    CONSTRAINT fk_heatzone_composition_decision_policy
        FOREIGN KEY (decision_policy_version_id, tenant_id)
        REFERENCES workflow.decision_policies(policy_version_id, tenant_id)
);

-- 一個網格在同一時點只能屬於一個生效中的熱區 (Active Unique Index)
CREATE UNIQUE INDEX IF NOT EXISTS idx_heatzone_composition_active_member
    ON expansion.heatzone_composition (tenant_id, member_cell_id)
    WHERE reverted_at IS NULL;

-- 支援可逆歷程與人工推翻稽核索引
CREATE INDEX IF NOT EXISTS idx_heatzone_composition_audit
    ON expansion.heatzone_composition (tenant_id, zone_id, decided_at);
```

### 5.2 反事實驗收標準（Counterfactual Acceptance Criteria）

下游模型實作完成後，必須通過下列反事實驗收測試，證明合併／拆分優於原子網格基準：

1. **排序品質增益（NDCG Outperformance）**：
   - 合併後熱區商圈對出樣門市營收表現之排序品質指標 NDCG，必須較未合併前之原子 H3 網格基準排序 **提高至少 $+0.05$**（例如 NDCG 自 0.52 提升至 ≥ 0.57）。
2. **稀釋預測誤差降低（Cannibalization Variance Reduction）**：
   - 跨相鄰網格之自體門市稀釋預測殘差變異數，在合併商圈下必須較獨立網格加總 **降低 ≥ 20%**。
3. **空間異質性／同質性統計檢定**：
   - **合併條件（Merge Rule）**：兩相鄰網格間之人口活動與營收關聯度 $\rho \ge 0.75$，且網格邊界間之需求落差斷裂指數 $< 0.20$。
   - **拆分條件（Split Rule）**：單一網格內部若存在天然阻隔（如主要幹道、鐵路、河道），且兩側之實績需求密度差異 $\ge 2.5\times$，方得拆分為 `SPLIT_CHILD`。

### 5.3 可逆撤銷與回滾機制（Rollback & Reversal Protocol）

1. **單步撤銷（Non-Destructive Soft Rollback）**：
   - 撤銷任何合併或拆分動作時，僅需執行 `UPDATE expansion.heatzone_composition SET reverted_at = CURRENT_TIMESTAMP WHERE zone_id = :target_zone_id AND reverted_at IS NULL`。
   - 資料庫即刻恢復各組成網格至獨立原子狀態（或原母商圈狀態），不遺失任何歷史評分數據與稽核軌跡。
2. **人工推翻留痕（Human Override Protocol）**：
   - 若業務主管或拓店人員依現場現勘推翻系統自動合併／拆分結果，必須記錄 `decided_by = <user_email>`、`override_reason = <詳細理由>`，並綁定當前生效之 `decision_policy_version_id`。
3. **影子評估期（Shadow Period）**：
   - 實作上線初期必須在 `ExecutionMode.SHADOW` 下運行至少 30 天，雙軌比對原子網格與合併熱區之評分與決策差異，經審查無異常後方得切換為 `ACTIVE`。

---

## 6. 結論與後續執行指引

1. **本任務目標已達成**：已依生產 HZ-004 實績現況完成客觀判定，確立四大準備度門檻，並留下完整的可逆契約與驗收規格。
2. **遵守安全與治理規則**：未引進任何假數據或未成熟之空間合併演算法，維持系統 fail-closed 安全狀態。
3. **後續追蹤**：由 Supervisor 與狀態系統登記此 blocker 至 `Human/Ops`，並於 `2026-12-01` 觸發季度實績重檢。
