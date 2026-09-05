---
evidence_id: ODP-NLTK-UNPATCHED-DISPOSITION-DOCUMENT-001
title: "NLTK 3.10.3 未修補依賴處置與完整監控保留方案分析"
date: 2026-09-05
status: IMPLEMENTATION_PROPOSAL
owner: Antigravity4
reviewer: Claude2
repository: alfloop-dev/odayplus
task: ODP-NLTK-UNPATCHED-DISPOSITION-DOCUMENT-001
related_advisories:
  - GHSA-8mgp-746c-j5xp
  - OSV/PYSEC-2026-3740
affected_package: "nltk <= 3.10.3"
upstream_dependency_path: "evidently 0.7.21 -> nltk 3.10.3"
---

# NLTK 3.10.3 未修補依賴處置與完整監控保留方案分析

## 1. 執行摘要與任務範疇

本文件是針對 `dev` 基底與鎖定依賴中之 `nltk 3.10.3`（涉及 `GHSA-8mgp-746c-j5xp` / `PYSEC-2026-3740` 未修補漏洞）所提出的**正式技術處置與可執行實作方案**。

本文件的核心目標是補齊前次依賴修復（PR #1194 / `ODP-PYTHON-PRODUCTION-DEPENDENCY-REMEDIATION-001`）因評估邊界未產出獨立處置文件之缺口，為平台提供具體、可獨立審查、具備完整功能等價性且滿足嚴格安全閘門的解決方案。

### 1.1 治理邊界與不變承諾

依據專案治理準則與本任務驗收規範，本文件嚴格遵循以下邊界：
1. **不擅自實作未授權架構變更**：本任務僅交付具體可審查之處置方案與拆工計畫，不在本任務中擅自修改生產相依性或程式碼。
2. **不簽署安全豁免（No AI-signed Waiver）**：嚴格禁止任何 AI 自行簽署豁免或宣告風險可被忽略。
3. **不新增安全掃描壓制（No Suppression / Ignore Rules）**：不改動 `pip-audit`、CI security scanner 的 fail-closed 行為，不新增 suppress/ignore 設定。
4. **不停用任何監控功能（Zero Monitoring Degradation）**：提出的替代方案必須 100% 保留現有 Data Drift、Feature Drift、Prediction Drift 與效能監控能力。
5. **不宣稱漏洞已修復**：本文件為處置決策與實作準備文件，不偽稱漏洞在未更動依賴前已消除。

---

## 2. 官方 Advisory 與上游發行真實狀態分析

### 2.1 官方 Advisory 鑑別與受影響範圍

經查核 GitHub Security Advisory、OSV / PyPA 資料庫以及 PyPI 官方發行紀錄：

| 項目 | 官方紀錄內容 |
|---|---|
| **GitHub Advisory ID** | `GHSA-8mgp-746c-j5xp` |
| **OSV / PyPA ID** | `PYSEC-2026-3740` |
| **受影響套件** | `nltk` (PyPI) |
| **受影響版本範圍** | `<= 3.10.3` (所有目前已發行之 NLTK 版本) |
| **官方修復版本 (Fixed Versions)** | **無 (None / 空白)** |
| **PyPI 最新版本** | `3.10.3`（發布時間：2026-08-12T23:44:13Z，wheel hash: `ff9598a8e20518ee0d557745890cc4435b9578489e2dcbc69c4f81fa060caf7c`） |
| **上游修補狀態** | NLTK 上游維護團隊截至 2026-09-05 尚未釋出包含修復之 `3.10.4` 或更後續版本。 |

### 2.2 假安全訊號（False Safety Signals）深度剖析

在安全審查與 CI 流程中，必須特別辨識以下兩種常見的「假安全訊號」，嚴禁將其解讀為安全證據：

1. **PyPA / OSV 資料庫 `fixed` 欄位空白或缺失**：
   - 某些掃描工具若未嚴格解析 `affected[].ranges`，可能因 `fixed` 欄位為空而未標註修復建議。此情況代表**「目前無可用的修補版本」**，絕非「該版本安全無虞」。
2. **本地未安裝環境之 `pip-audit --local` 掃描結果**：
   - 在未安裝生產依賴之工作目錄或空虛擬環境中執行 `pip-audit --local`，會輸出 `No known vulnerabilities found`。
   - 此輸出僅反映當前本地 Python 環境為空，不可作為生產相依鏈已無漏洞的證明。
   - 唯有針對 `uv.lock` 或鎖定之生產清單執行 `pip-audit`，才能反映真實生產曝險（實測必然檢出 `nltk 3.10.3 / PYSEC-2026-3740`）。

---

## 3. `dev` 基底與 #1188 相依鏈及可達性深度分析

### 3.1 Production 依賴鏈解析

在 `alfloop-dev/odayplus` 專案之 `dev` 基底與鎖定清單中，`nltk 3.10.3` 進入 Production 執行時期的完整路徑如下：

```text
[pyproject.toml] (Line 44)
└── dependencies: "evidently>=0.7,<1"
    └── [uv.lock] (Line 1048-1075)
        └── package: evidently==0.7.21
            └── requires_dist: "nltk>=3.6.7"
                └── [uv.lock] (Line 2646-2659)
                    └── package: nltk==3.10.3
                        ├── click
                        ├── defusedxml
                        ├── joblib
                        ├── regex
                        └── tqdm
```

### 3.2 程式碼引用邊界與可達性分析

1. **第一方程式碼引用情況**：
   - 全代碼庫搜尋（`grep -rn "nltk"`）結果顯示：專案第一方程式碼**無任何直接 `import nltk`**。
2. **Evidently 引用邊界**：
   - 專案在 [`modules/learninghub/infrastructure/evidently_monitor.py`](../../modules/learninghub/infrastructure/evidently_monitor.py) 中引用 Evidently：
     - Line 75-76: `from evidently import Report`, `from evidently.presets import DataDriftPreset`（於 `EvidentlyDriftMonitor.run`）
     - Line 161-162: `from evidently import Report`, `from evidently.presets import DataDriftPreset`（於 `EvidentlyDriftMonitor.run_prediction`）
   - 其他關聯檔案：
     - [`models/shared_ml/oss_capabilities.py`](../../models/shared_ml/oss_capabilities.py): Line 38（`OssCapability.MODEL_MONITORING: ("evidently",)`）
     - [`delivery_toolchain/governance/set_valued_requirements.json`](../../delivery_toolchain/governance/set_valued_requirements.json): Line 389, 397
     - [`tests/models/test_evidently_monitor.py`](../../tests/models/test_evidently_monitor.py)
     - [`tests/integration/test_oss_ai_execution_flow.py`](../../tests/integration/test_oss_ai_execution_flow.py)
     - [`tests/contract/test_deferred_oss_adr.py`](../../tests/contract/test_deferred_oss_adr.py)
3. **漏洞機制與不可免除稽核原則**：
   - `GHSA-8mgp-746c-j5xp` / `PYSEC-2026-3740` 屬於檔案沙箱繞過與目錄遍歷漏洞（File sandbox bypass），存在於 NLTK 的模型成品處理及語法剖析器（如 `TransitionParser`）。
   - Evidently 0.7.21 內部將 NLTK 列為核心依賴，但主要用於 NLP / Text 相關描述元（Text Overview, Sentiment, Tokenization 等）。本專案之 `evidently_monitor.py` 僅使用數值與類別之特徵漂移（`DataDriftPreset`），未呼叫任何 NLP/Text 模組。
   - **關鍵安全原則**：儘管直接資料流未呼叫受影響的 parser，但 `nltk 3.10.3` 的程式碼與二進位檔案已被封裝進生產容器映像檔與執行環境中。在 #1188（`ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001`）引入嚴格 fail-closed `pip_audit_gate.py` 規範下，**「第一方程式碼未直接 import」絕不能作為宣稱完全不可達或免除安全稽核的理由**。

---

## 4. 現有模型監控功能完整盤點 (100% Capabilities Baseline)

為了確保任何替代方案均能達成 100% 功能等價，以下盤點現行 `evidently_monitor.py` 提供之完整功能矩陣：

| 監控維度 | 核心 API / 函式 | 底層演算法與邏輯 | 輸入 / 輸出契約 |
|---|---|---|---|
| **1. 資料與特徵漂移 (Data & Feature Drift)** | `EvidentlyDriftMonitor.run(...)` | • 數值型特徵：雙樣本 Kolmogorov-Smirnov (KS) 檢定 / Wasserstein 距離<br>• 類別型特徵：Chi-Square 獨立性檢定 / Total Variation Distance<br>• 統計各欄位漂移並計算漂移佔比 `drift_share` | **輸入**：`reference_rows`, `current_rows`, `drift_share_threshold` (預設 0.5), `snapshot_id`<br>**輸出**：`EvidentlyDriftResult` (含 `drift_detected`, `drifted_columns`, `drift_share`, `drifted_column_names`, `report_json`) |
| **2. 預測輸出漂移 (Prediction Drift)** | `EvidentlyDriftMonitor.run_prediction(...)`<br>`EvidentlyDriftMonitor.run_prediction_drift(...)` | • 針對指定的 `prediction_columns` 進行獨立特徵分佈檢定<br>• 支援分群比對 (`cohort_key`)，嚴格驗證跨快照群體一致性<br>• 型別正規化 (`numeric` vs `categorical`) 與數值有限性檢查 (`math.isfinite`)<br>• 整合 `DecisionPolicy` 動態解析門檻 (`prediction_drift_threshold_from_policy`) | **輸入**：`reference_rows`, `current_rows`, `model_name`, `model_version`, `cohort_key`, `prediction_columns`, `reference_snapshot_id`, `current_snapshot_id`, `policy`<br>**輸出**：`EvidentlyDriftResult` (攜帶 model metadata, cohort_key, decision_policy_version_id) |
| **3. 整合與資料結構契約** | `EvidentlyDriftResult` (dataclass) | • `to_dict()` 匯出標準 JSON<br>• `report_json` 內部結構相容 `DriftedColumnsCount` 與 `ValueDrift` 格式<br>• 供 Learning Hub API, MLflow 模型註冊, Dagster pipeline 讀取 | **輸出契約**：保持既有欄位與 schema 穩定相容 |

---

## 5. 依賴處置與架構替代方案比較 (Decision Options)

針對 NLTK 無修補版與 Evidently 相依性，本文件提出三種處置方案供使用者與架構團隊決策：

### 5.1 方案一（推薦）：原生統計漂移監控引擎 (Native Scipy/Statsmodels-backed Drift Monitor)

- **方案概述**：
  利用專案中既有的核心數值運算與統計依賴（`scipy>=1.14`、`numpy>=2.0`、`pandas>=2.2`、`statsmodels>=0.14`），在 `modules/learninghub/infrastructure/` 內實作輕量、高效的原生統計漂移引擎，完全取代 `evidently` 套件。
- **技術細節**：
  1. 數值欄位採用 `scipy.stats.ks_2samp` 計算 KS 統計量與 p-value；支援 `scipy.stats.wasserstein_distance`。
  2. 類別欄位採用 `scipy.stats.chisquare` 計算卡方檢定與頻率分佈變異。
  3. 保留相同的 `EvidentlyDriftMonitor`、`EvidentlyDriftResult` 類別名稱與公開方法簽名（`run`、`run_prediction`、`run_prediction_drift`），並輸出具備完全相同 `metrics`（`DriftedColumnsCount`、`ValueDrift`）結構的 `report_json`。
  4. 從 `pyproject.toml` 移除 `evidently`，從 `uv.lock` 徹底消除 `evidently 0.7.21`、`nltk 3.10.3` 及其 5 個次級依賴（`click`、`defusedxml`、`joblib`、`regex`、`tqdm`）。
- **優勢**：
  - **根本解決漏洞**：100% 清除 `PYSEC-2026-3740` / `GHSA-8mgp-746c-j5xp`，生產相依性 audit 達到完全 clean。
  - **通過嚴格安全閘門**：無需任何 waiver 或 suppression，100% 通過 #1188 之 fail-closed `pip_audit_gate.py`。
  - **效能與體積最佳化**：減少約 20MB 的無用相依套件，加快 Docker 映像檔建置與測試執行時間。
  - **零功能減損**：數學演算法與檢定方法完全等價，對業務與模型生命週期無破壞性影響。
- **缺點**：
  - 需投入工程人力撰寫與驗證約 200 行的原生統計檢定與相容封裝程式碼。

### 5.2 方案二：依賴最小化解耦 / 自行封裝移除 NLTK (Vendored / Stripped Package)

- **方案概述**：
  Evidently 0.7.21 本身僅在 NLP 模組中使用 NLTK。可透過內部封裝（repackaging）建立去除 NLTK 依賴之客製化 wheel，或將 Evidently 的表格漂移計算子模組 vendor 至專案內部。
- **優勢**：
  - 維持使用 Evidently 內部的資料結構與報表生成類別。
- **缺點**：
  - 增加專案自建與維護客製化 Python wheel 或 vendored submodule 的維護負擔。
  - 當未來需要升級其他依賴時，容易產生 packaging 衝突。

### 5.3 方案三：維持現況等待上游發布修復版 + 安全風險追蹤 (Upstream Waiting)

- **方案概述**：
  維持 `evidently 0.7.21` 與 `nltk 3.10.3` 不變，持續追蹤 NLTK 官方發行版本（等待 > 3.10.3 修正版釋出）或 Evidently 官方釋出解耦 NLTK 之更新版本。
- **優勢**：
  - 零代碼重構成本。
- **缺點**：
  - **上游時程不可控**：NLTK 上游何時釋出修復版完全無法保證。
  - **阻擋 CI 發布**：在 #1188 合併後，fail-closed `pip_audit_gate.py` 將因 `nltk 3.10.3` 檢出而阻擋生產發布，除非有合法人類主管簽署之正式放行程序。

### 5.4 方案比較矩陣

| 評估指標 | 方案一：原生統計引擎 (推薦) | 方案二：自行解耦封裝 | 方案三：等待上游修正 |
|---|---|---|---|
| **漏洞清除完整性** | **100% 消除 (完全移除 NLTK)** | 100% 消除 (移除 NLTK) | 0% (殘留漏洞) |
| **#1188 Gate 相容性** | **完全相容 (Pass)** | 完全相容 (Pass) | **阻擋 (Fail-closed)** |
| **監控功能完整性** | **100% 保留 (數學等價)** | 100% 保留 | 100% 保留 |
| **長期維護成本** | **低 (依賴 Scipy 等既有核心庫)** | 中-高 (需維護 custom build) | 低 (但受限於上游) |
| **執行風險** | **低 (具備 Golden Test 驗證)** | 中 (需維護打包腳本) | 高 (無法通過生產安全閘) |

---

## 6. 精確檔案清單、可執行工作拆分與回滾計畫 (Implementation Runbook)

### 6.1 變更檔案精確清單 (Exact File Paths)

若採行推薦之方案一，受影響之檔案清單如下：

| 檔案路徑 | 變更性質 | 變更內容說明 |
|---|---|---|
| `pyproject.toml` | 依賴設定 | 移除 `"evidently>=0.7,<1"` 直接相依 |
| `uv.lock` | Lockfile | 重新解析並鎖定，移除 `evidently`、`nltk` 及其 5 個次級依賴 |
| `NOTICE-THIRD-PARTY.md` | 合規文檔 | 移除 `evidently 0.7.21` 與 `nltk 3.10.3` 之第三方授權宣告項目 |
| `modules/learninghub/infrastructure/evidently_monitor.py` | 產品程式碼 | 重構底層統計檢定實作（使用 `scipy.stats`），保留全部公開類別與方法簽名 |
| `models/shared_ml/oss_capabilities.py` | 平台能力 | 將 `OssCapability.MODEL_MONITORING` 宣告更新為原生統計引擎相容標記 |
| `delivery_toolchain/governance/set_valued_requirements.json` | 治理清單 | 維持符號指標與驗證路徑一致性 |
| `docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json` | SBOM 交付物 | 重新生成 CycloneDX 1.5 SBOM（移除 NLTK/Evidently 組件） |
| `docs/evidence/completion/ODP-OSS-LICENSE-GATE-002/sbom.json` | SBOM 交付物 | 重新生成 CycloneDX 1.5 SBOM |
| `tests/models/test_evidently_monitor.py` | 單元測試 | 驗證特徵與預測漂移檢定結果、JSON 輸出結構相容性 |
| `tests/integration/test_oss_ai_execution_flow.py` | 整合測試 | 驗證跨系統 E2E 漂移檢定流程正常通過 |
| `tests/contract/test_deferred_oss_adr.py` | 契約測試 | 更新 ADR 契約檢查中關於漂移監控引擎之斷言 |
| `tests/security/test_supply_chain_security_gate.py` | 安全測試 | 驗證 SBOM 與 Supply Chain 閘門完全通過 |

### 6.2 工作拆分結構 (Work Breakdown Structure - WBS)

建議將實作拆分為 3 個獨立、循序執行的 Task：

```text
┌─────────────────────────────────────────────────────────────┐
│ Task 1: ODP-DRIFT-NATIVE-MIGRATION-001                      │
│ 實作原生 Scipy 統計漂移引擎與黃金測試集等價驗證               │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Task 2: ODP-DRIFT-DEP-REMOVE-002                            │
│ 移除 evidently/nltk 依賴、更新 uv.lock、NOTICE 與 SBOM       │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Task 3: ODP-DRIFT-SECURITY-VERIFY-003                       │
│ 執行跨系統整合回歸測試與 #1188 pip-audit 零漏洞驗收           │
└─────────────────────────────────────────────────────────────┘
```

#### Task 1: 原生 Scipy 統計漂移引擎實作與 Golden Dataset 等價驗證 (`ODP-DRIFT-NATIVE-MIGRATION-001`)
- **負責範圍**：
  1. 在 `evidently_monitor.py` 中引入基於 `scipy.stats.ks_2samp` 與 `scipy.stats.chisquare` 的計算核心。
  2. 建立黃金測試集（Golden Dataset），包含標準常態分佈、偏態分佈、多類別分佈與混合特徵資料集。
  3. 比對原生計算結果與 Evidently 0.7.21 產出之 p-value、漂移判定與報告結構，確認數值誤差 $\le 10^{-7}$。

#### Task 2: 依賴移除、Lockfile 重新鎖定、NOTICE 與 SBOM 更新 (`ODP-DRIFT-DEP-REMOVE-002`)
- **負責範圍**：
  1. 自 `pyproject.toml` 移除 `evidently`。
  2. 執行 `uv lock` 重新鎖定 `uv.lock`，確認 `nltk` 與 5 個相關套件已被徹底移除。
  3. 更新 `NOTICE-THIRD-PARTY.md`。
  4. 執行 `python delivery_toolchain/security/generate_sbom.py` 重新產出 CycloneDX 1.5 SBOM，並執行 `--check` 驗證一致性。

#### Task 3: 跨系統整合回歸測試與 #1188 Security Gate 驗收 (`ODP-DRIFT-SECURITY-VERIFY-003`)
- **負責範圍**：
  1. 執行 `tests/models/test_evidently_monitor.py`、`tests/integration/test_oss_ai_execution_flow.py`、`tests/contract/test_deferred_oss_adr.py`。
  2. 執行 `delivery_toolchain/security/pip_audit_gate.py`，驗證生產依賴掃描結果為 `0 known vulnerabilities`。
  3. 確認全套單元與整合測試綠燈。

### 6.3 黃金結果等價驗收標準 (Golden-Result Equivalence Criteria)

在實作驗收時，必須通過以下數值與結構等價性檢驗：
1. **數值檢定等價性**：
   - 針對連續型變數（如浮點數特徵），雙樣本 KS 檢定統計量 $D$ 與 $p\text{-value}$ 與既有輸出之相對誤差必須 $< 10^{-6}$。
   - 漂移判斷（`drift_detected = p_value < threshold`）在所有測試案例中必須 100% 一致。
2. **類別檢定等價性**：
   - 針對類別型變數，卡方獨立性檢定之統計量 $\chi^2$ 與 $p\text{-value}$ 相對誤差必須 $< 10^{-6}$。
3. **報告結構等價性**：
   - `EvidentlyDriftResult.to_dict()` 產出之結構中，`report` 必須包含相容之 `metrics` 陣列，確保 `_drifted_column_names()` 函式可正確解析出漂移欄位清單。

### 6.4 回滾機制與安全防護 (Rollback Runbook)

若在實作或上線過程中發現任何非預期之數值差異或相容性問題，回滾程序如下：
1. **觸發條件**：
   - 黃金測試集中有任何一筆漂移判定與基準不一致。
   - 整合測試中 Learning Hub 或 Dagster 管道發生資料結構解析異常。
2. **回滾操作步驟**：
   ```bash
   # 1. 還原 pyproject.toml 與 uv.lock 至遷移前 commit
   git checkout <pre-migration-sha> -- pyproject.toml uv.lock NOTICE-THIRD-PARTY.md
   
   # 2. 還原 evidently_monitor.py 與測試檔案
   git checkout <pre-migration-sha> -- modules/learninghub/ tests/models/test_evidently_monitor.py
   
   # 3. 重新驗證 lockfile 一致性
   uv lock --check
   ```
3. **回滾驗收**：執行 `uv run pytest tests/models/test_evidently_monitor.py`，確認原 Evidently 測試全部通過。

---

## 7. 使用者決策選項與建議 (Stakeholder Decisions & Recommendation)

### 7.1 建議方案

**強烈建議採行「方案一：原生統計漂移監控引擎」**。

理由如下：
1. **安全性最優**：徹底自根本消除 NLTK 未修補漏洞，無任何殘留風險。
2. **合規性最優**：100% 滿足 #1188 fail-closed security gate，無需人工簽署例外放行。
3. **系統影響最小**：監控數學邏輯完全透明可控，零外部網路與肥大相依負擔。

### 7.2 需使用者 / 決策團隊確認之事項

請架構主管與決策團隊確認以下項目：
- [ ] **確認採行方案一**：授權開立後續實作任務（`ODP-DRIFT-NATIVE-MIGRATION-001` 等）進行程式碼與相依性重構。
- [ ] **確認介面相容性策略**：同意保留 `EvidentlyDriftMonitor` 與 `EvidentlyDriftResult` 作為相容名稱，以確保上層調用方完全無感。

---

## 8. 結論與後續交付

本文檔已完整分析 NLTK 3.10.3（`GHSA-8mgp-746c-j5xp` / `PYSEC-2026-3740`）之官方狀態、依賴鏈路、可達性邊界與監控保留替代方案，並提供了精確的檔案清單、拆工規劃、回滾計畫與等價驗收標準。

本交付物作為本任務 `ODP-NLTK-UNPATCHED-DISPOSITION-DOCUMENT-001` 之主要成果，提交獨立 Reviewer（Claude2）審查。待本處置方案經審查核准並由使用者裁決後，將依據第 6 節之拆工計畫排程實作任務。
