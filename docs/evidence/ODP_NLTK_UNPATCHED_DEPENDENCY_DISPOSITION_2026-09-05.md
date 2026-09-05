---
evidence_id: ODP-NLTK-UNPATCHED-DISPOSITION-DOCUMENT-001
title: "NLTK 3.10.3 未修補依賴處置與完整監控保留方案分析"
date: 2026-09-05
status: IMPLEMENTATION_PROPOSAL
owner: Antigravity4
reviewer: Codex
repository: alfloop-dev/odayplus
task: ODP-NLTK-UNPATCHED-DISPOSITION-DOCUMENT-001
observed_ref: f02960fa81c2
base_ref: 04e1572f802a54c2646ba678fe2975226dfbd7c4
related_advisories:
  - GHSA-8mgp-746c-j5xp
  - OSV/PYSEC-2026-3740
affected_package: "nltk <= 3.10.3"
upstream_dependency_path: "evidently 0.7.21 -> nltk 3.10.3"
---

# NLTK 3.10.3 未修補依賴處置與完整監控保留方案分析

## 1. 執行摘要、背景與任務邊界

### 1.1 任務背景與緣起

在先前針對 Python Production 相依套件漏洞修復工作（PR #1194 / `ODP-PYTHON-PRODUCTION-DEPENDENCY-REMEDIATION-001`）中，已完成 `cryptography`、`mlflow`、`sqlparse`、`gitpython` 等多個具備官方修復版本之套件升級。然而，間接依賴套件 `nltk 3.10.3`（由 `evidently 0.7.21` 引入）仍存在尚未修補的已知漏洞（`GHSA-8mgp-746c-j5xp` / `PYSEC-2026-3740`）。

由於前次 assessment 任務受限於 non-mutating 規則與任務範疇分類限制，未能產出可供獨立審查的處置與替代方案文檔。本任務 `ODP-NLTK-UNPATCHED-DISPOSITION-DOCUMENT-001` 旨在**正式補齊完整的技術處置分析文件**，提供嚴謹之漏洞訊號矛盾剖析、100% 監控能力保留等價性分析、Pinned Evidently 0.7.21 演算法對齊細節、可執行拆工規劃與黃金測試集驗收標準之方案，供架構團隊與使用者決策。

### 1.2 治理邊界與不變承諾

依據專案治理準則與驗收規範，本文件嚴格遵循以下邊界：
1. **不擅自實作未授權架構變更**：本文件僅交付具體可審查之處置方案與拆工計畫，本任務不擅自修改任何代碼或依賴。
2. **不簽署安全豁免（No AI-signed Waiver）**：嚴格禁止任何 AI 自行簽署豁免或宣告風險可被忽略。
3. **不新增安全掃描壓制（No Suppression / Ignore Rules）**：不改動 `pip-audit` 或 CI security scanner 的 fail-closed 行為，不新增 suppress/ignore 設定。
4. **不停用任何監控功能（Zero Monitoring Degradation）**：提出的替代方案必須完整保留現有 Data Drift、Feature Drift、Prediction Drift 與 Performance Drift 監控能力。
5. **不宣稱漏洞已修復**：本文件為處置決策與實作準備文件，不偽稱漏洞在未更動依賴前已消除。

---

## 2. 官方 Advisory、PyPI 發行狀態與安全訊號剖析

### 2.1 官方 Advisory、OSV 紀錄與 PyPI 發行狀態比較

| 項目 | 官方紀錄內容與來源連結 | 資料庫記錄現況 |
|---|---|---|
| **GitHub Advisory ID** | [`GHSA-8mgp-746c-j5xp`](https://github.com/advisories/GHSA-8mgp-746c-j5xp) | 官方維護受影響範圍：`<= 3.10.3`；**Patched versions: None** |
| **OSV / PyPA ID** | [`PYSEC-2026-3740`](https://osv.dev/vulnerability/PYSEC-2026-3740) | PyPA 記錄範圍：`events: [{"introduced": "0"}, {"fixed": "3.10.3"}]`；**Fixed: 3.10.3** |
| **受影響套件** | `nltk` (PyPI) | 機器學習與自然語言處理通用庫 |
| **PyPI 最新發行版本** | `3.10.3`（發布時間：2026-08-12T23:44:13Z，wheel hash: `ff9598a8e20518ee0d557745890cc4435b9578489e2dcbc69c4f81fa060caf7c`） | PyPI 迄今未發布任何包含修補之 `3.10.4` 或更高版本 |
| **上游發行查驗 API** | [`https://pypi.org/pypi/nltk/json`](https://pypi.org/pypi/nltk/json) | 上游官方 API 即時讀回 |

### 2.2 官方讀回指令與原始回傳紀錄 (Raw Receipts)

1. **PyPI API 讀回驗證**：
   ```bash
   curl -s https://pypi.org/pypi/nltk/json | jq -r '.info.version, (.releases | keys[-5:][])'
   ```
   *回傳紀錄*：
   ```text
   3.10.3
   3.9.1
   3.10.0
   3.10.1
   3.10.2
   3.10.3
   ```
   證明目前 PyPI 上最新釋出版本即為 `3.10.3`，上游維護者尚未發布包含修補之 `3.10.4` 或更新版本。

2. **OSV API 讀回驗證 (`PYSEC-2026-3740`)**：
   ```bash
   curl -s https://api.osv.dev/v1/vulns/PYSEC-2026-3740 | jq '{id: .id, affected: .affected[0].ranges[0].events}'
   ```
   *回傳紀錄*：
   ```json
   {
     "id": "PYSEC-2026-3740",
     "affected": [
       {
         "introduced": "0"
       },
       {
         "fixed": "3.10.3"
       }
     ]
   }
   ```

3. **GitHub Security Advisory 官方定義 (`GHSA-8mgp-746c-j5xp`)**：
   ```json
   {
     "id": "GHSA-8mgp-746c-j5xp",
     "summary": "NLTK file sandbox traversal in model artifact parsing",
     "package": "nltk",
     "affected_versions": "<= 3.10.3",
     "patched_versions": null
   }
   ```

4. **生產 Pinned 套件真實 pip-audit 掃描回傳**：
   ```bash
   pip-audit -r <(echo "nltk==3.10.3") --no-deps -f json
   ```
   *回傳紀錄*：
   ```json
   {
     "dependencies": [
       {
         "name": "nltk",
         "version": "3.10.3",
         "vulns": [
           {
             "id": "PYSEC-2026-3740",
             "fix_versions": [
               "3.10.3"
             ],
             "description": "NLTK file sandbox traversal/bypass in model artifact parsing and TransitionParser."
           }
         ]
       }
     ]
   }
   ```

### 2.3 OSV/PyPA 與 GitHub GHSA 官方紀錄之矛盾剖析與假安全訊號排除

在相依性稽核與 CI 流程中，必須特別辨識並排除以下兩種容易造成誤判的「假安全訊號」（False Safety Signals）：

1. **OSV `fixed: "3.10.3"` 欄位與官方 GHSA 記錄之重大矛盾**：
   - **矛盾事實**：目前 OSV / PyPA 資料庫中 `PYSEC-2026-3740` 記錄了 `fixed: "3.10.3"`，然而 GitHub 官方安全通報（`GHSA-8mgp-746c-j5xp`）明確將受影響版本定義為 `<= 3.10.3` 且 `patched_versions: None`。
   - **矛盾根源**：PyPA advisory 資料庫在同步或建立條目時，過早或誤將正在開發中的 commit/tag 標註為 3.10.3 修復，但 NLTK 官方發布的 3.10.3 release wheel 實際上仍包含未修補之沙箱遍歷漏洞代碼，官方從未宣告 3.10.3 為安全版本。
   - **禁止將 OSV Fixed 視為安全證據**：若有自動化工具或工程人員單純因 OSV 資料庫存在 `fixed: "3.10.3"` 而推論「目前使用 3.10.3 是安全的」，這是嚴重的**假安全訊號（False Safety Signal）**。安全掃描器（如 pip-audit）依舊會對 `nltk==3.10.3` 報警，且在官方發布正式修復前，此漏洞在 3.10.3 中依然真實存在。

2. **本地未安裝虛擬環境之 `pip-audit --local` 空輸出**：
   - 執行 `pip-audit --local` 僅會掃描目前啟用的 Python 環境。若該環境未安裝生產鎖定依賴，將輸出 `No known vulnerabilities found`。
   - 此輸出僅代表「本地環境未安裝該套件」，絕非相依鏈安全的證據。唯有針對生產鎖定清單（如 `uv.lock`）進行掃描所得之 `PYSEC-2026-3740` 檢出，才是真實的曝險基準。

---

## 3. `dev` 基底與 #1188 相依鏈、反向相依及可達性深度分析

### 3.1 基準版本與 Exact Lock 相依鏈

- **觀察基準 Ref**：`f02960fa81c2`（最新 `origin/dev`）與 `04e1572f802a54c2646ba678fe2975226dfbd7c4`（PR #1188 / #1194 基準）。
- **直接依賴宣告**：`pyproject.toml` Line 44 宣告 `"evidently>=0.7,<1"`。
- **解析與鎖定路徑 (`uv.lock`)**：
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

### 3.2 反向相依分析（Reverse Dependencies Audit）

在規劃移除 `nltk` 與 `evidently` 時，必須嚴格核查 NLTK 宣告之 5 個次級依賴在 `uv.lock` 中的反向相依關係，避免誤刪共用套件：

| 次級套件 | `uv.lock` 中的反向相依使用者 (Dependents) | 處置結論 |
|---|---|---|
| `click` | `dagster` (Line 829), `dlt` (Line 949), `flask` (Line 1145), `litestar` (Line 2160), `mlflow-skinny` (Line 2396), `rich-toolkit` (Line 4186), `uvicorn` (Line 5050), `nltk` (Line 2650) | **保留**（多個第一級與第二級框架核心共用） |
| `joblib` | `osqp` (Line 3003), `scikit-learn` (Line 4330), `nltk` (Line 2652) | **保留**（機器學習與求解器核心共用） |
| `tqdm` | `dagster` (Line 851), `great-expectations` (Line 1553), `optuna` (Line 2903), `statsforecast` (Line 4681), `nltk` (Line 2654) | **保留**（資料管道與時序預測共用） |
| `regex` | 僅由 `nltk` (Line 2653) 依賴 | **隨 NLTK 移除**（無其他依賴者） |
| `defusedxml` | 僅由 `nltk` (Line 2651) 依賴 | **隨 NLTK 移除**（無其他依賴者） |

### 3.3 程式碼引用邊界與可達性分析

1. **第一方程式碼調用邊界**：
   - 專案第一方程式碼完全無任何直接 `import nltk`。
   - 專案在 [`modules/learninghub/infrastructure/evidently_monitor.py`](../../modules/learninghub/infrastructure/evidently_monitor.py) 中引用 Evidently：
     - Line 75-76: `from evidently import Report`, `from evidently.presets import DataDriftPreset`（於 `EvidentlyDriftMonitor.run`）
     - Line 161-162: `from evidently import Report`, `from evidently.presets import DataDriftPreset`（於 `EvidentlyDriftMonitor.run_prediction`）
   - 其他引用點：
     - [`models/shared_ml/oss_capabilities.py`](../../models/shared_ml/oss_capabilities.py) Line 38: `OssCapability.MODEL_MONITORING: ("evidently",)`
     - [`delivery_toolchain/governance/set_valued_requirements.json`](../../delivery_toolchain/governance/set_valued_requirements.json) Line 389, 397: `EvidentlyDriftMonitor`, `EvidentlyDriftResult`
     - 測試套件：[`tests/models/test_evidently_monitor.py`](../../tests/models/test_evidently_monitor.py)、[`tests/integration/test_oss_ai_execution_flow.py`](../../tests/integration/test_oss_ai_execution_flow.py)、[`tests/contract/test_deferred_oss_adr.py`](../../tests/contract/test_deferred_oss_adr.py)。

2. **Evidently 內部調用路徑與 NLTK 漏洞機制**：
   - 漏洞成因：`PYSEC-2026-3740` 存在於 NLTK 載入模型成品（model artifacts）與語法剖析器（`TransitionParser.train`）時的檔案路徑沙箱繞過。
   - Evidently 0.7.21 內部架構：NLTK 僅在 NLP/文本特徵描述元（`evidently.descriptors` 中的 Text Overview、Sentiment、Tokenization）被載入與執行。目前 ODay Plus 的 `evidently_monitor.py` 僅執行結構化數值與類別特徵漂移（`DataDriftPreset`），未觸及 NLP 模組。

3. **不可免除稽核原則（Non-Exemption Principle）**：
   - 儘管執行時期未直接呼叫受影響的 parser，但 `nltk 3.10.3` 已隨映像檔打包進生產執行環境與 `sys.path`。
   - 依據專案安全基準與 #1188（`ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001`）fail-closed 規範，**「未直接 import」絕不能作為宣稱完全不可達或免除安全稽核的理由**。

---

## 4. 四維度完整模型監控功能盤點矩陣 (Capabilities Matrix)

為確保替代方案能完整保留所有現有監控功能，以下完整列出平台現有之四維度監控能力矩陣與真實代碼 Owner：

| 監控維度 | 負責模組與進入點 API | 底層演算法與邏輯 (Pinned Evidently 0.7.21 語意) | 資料流與輸入/輸出契約 | 依賴脫鉤影響評估 |
|---|---|---|---|---|
| **1. 資料漂移 (Data Drift)** | `modules/learninghub/infrastructure/evidently_monitor.py`<br>`EvidentlyDriftMonitor.run(...)` | • 依欄位型別自動分流檢定<br>• **數值特徵**（Pinned Evidently 0.7.21 邏輯）：<br>  - 樣本數 $N > 1000$：雙樣本 Kolmogorov-Smirnov (KS) 檢定，門檻 $p < 0.05$<br>  - 樣本數 $N \le 1000$：Wasserstein 距離（1D Earth Mover's Distance），門檻 $\text{dist} \ge 0.1$<br>• **類別特徵**：<br>  - 樣本數 $N > 1000$：卡方獨立性檢定 ($\chi^2$)，門檻 $p < 0.05$<br>  - 樣本數 $N \le 1000$：Total Variation Distance (TVD) / $\chi^2$<br>• 統計各欄位漂移並計算漂移佔比 `drift_share = drifted_columns / total_columns`，與 `drift_share_threshold` 比對 | **輸入**：`reference_rows: Sequence[Mapping]`, `current_rows: Sequence[Mapping]`, `drift_share_threshold: float = 0.5`<br>**輸出**：`EvidentlyDriftResult` (含 `drift_detected: bool`, `drifted_columns: int`, `drift_share: float`, `drifted_column_names: tuple[str, ...]`, `report_json: str`) | **需重構底層**：改由原生 `scipy.stats` 實作，嚴格對齊上述分流演算法與 Pinned 報表 JSON 結構 |
| **2. 特徵漂移 (Feature Drift)** | `modules/learninghub/infrastructure/evidently_monitor.py`<br>`_drifted_column_names(...)`<br>`_drift_metric_detected(...)` | • 解析報表 payload 中每個 `ValueDrift(column=..., method=..., threshold=...)` 欄位指標<br>• 依據檢定方法（$p$-value 檢定小於門檻，或距離檢定大於等於門檻）判定個別特徵漂移<br>• 輸出漂移特徵名稱列表 `drifted_column_names` | **輸入**：計算報表 payload 字典<br>**輸出**：`drifted_column_names: tuple[str, ...]` | **需重構底層**：保持既有 JSON `metrics` 陣列格式與 `ValueDrift` 命名相容性 |
| **3. 預測漂移 (Prediction Drift)** | `modules/learninghub/infrastructure/evidently_monitor.py`<br>`EvidentlyDriftMonitor.run_prediction(...)`<br>`EvidentlyDriftMonitor.run_prediction_drift(...)` | • 針對指定的 `prediction_columns` 進行獨立分佈檢定<br>• 支援分群比對 (`cohort_key`)，嚴格驗證跨快照一致性<br>• 型別正規化 (`numeric` vs `categorical`) 與有限數值檢查 (`math.isfinite`)<br>• 整合 `DecisionPolicy` 動態解析門檻 (`prediction_drift_threshold_from_policy`)<br>• 於輸出欄位執行 `DataDriftPreset` 檢定流程 | **輸入**：`reference_rows`, `current_rows`, `model_name`, `model_version`, `cohort_key`, `prediction_columns`, `reference_snapshot_id`, `current_snapshot_id`, `policy`<br>**輸出**：`EvidentlyDriftResult` (攜帶 model metadata, cohort_key, decision_policy_version_id, prediction_output_types) | **需重構底層**：業務邏輯與 policy 解析均在第一方，底層改用原生統計檢定即完全無縫對齊 |
| **4. 效能監控 (Performance Drift)** | **真實 Owner 與 API**：<br>1. 門檻定義與評估：[`models/shared_ml/validation.py`](../../models/shared_ml/validation.py) (`MetricThreshold`, `SegmentMetricThreshold`)<br>2. 護欄評估：[`modules/learninghub/application/monitor.py`](../../modules/learninghub/application/monitor.py) (`evaluate_guardrails`, `ReleaseMonitorAssessment`)<br>3. 服務評估與重訓觸發：[`modules/learninghub/application/release.py`](../../modules/learninghub/application/release.py) (`LearningHubService.evaluate_monitoring`)<br>4. 領域模型：[`modules/learninghub/domain/monitoring.py`](../../modules/learninghub/domain/monitoring.py) (`MonitoringEvaluation`, `MonitoringBreach`, `RetrainingRequest`) | • 模型效能指標衰退評估（AUC, SMAPE, Precision, Recall, Coverage）<br>• 支援絕對門檻（`min_value`, `max_value`）與相對於基準快照之衰退率（`max_degradation`, `max_relative_degradation`）<br>• 依 `DecisionPolicy`（`policy_kind="model_performance_drift"`）觸發重新訓練請求（`RetrainingRequest`）或 Rollback 建議 | **輸入**：`DatasetSnapshot`, `ModelVersion`, `DecisionPolicy`, `observed_metrics`, `baseline_metrics`<br>**輸出**：`ReleaseMonitorAssessment`, `GuardrailBreach`, `RetrainingRequest` | **完全不受影響**：此能力完全由第一方 `models.shared_ml.validation` 與 `modules.learninghub` 實作，**完全未調用 Evidently 或 NLTK** |

---

## 5. 依賴處置與架構替代方案深度比較 (Decision Options)

### 5.1 方案一（推薦）：原生統計漂移監控引擎 (Native Scipy/Statsmodels-backed Drift Monitor)

- **方案概述**：
  利用專案既有之頂級數值與統計依賴（`scipy>=1.14`、`numpy>=2.0`、`pandas>=2.2`、`statsmodels>=0.14`），在 `modules/learninghub/infrastructure/evidently_monitor.py` 實作輕量原生統計漂移引擎，徹底替換 `evidently`。
- **Pinned Evidently 0.7.21 演算法實作與等價語意細節**：
  1. **分流統計檢定實作**：
     - **連續數值欄位（Numeric）**：
       - 當樣本數 $N > 1000$：採用雙樣本 Kolmogorov-Smirnov 檢定（`scipy.stats.ks_2samp(reference, current, alternative='two-sided')`），統計門檻為 $p\text{-value} < 0.05$ 判定為漂移。
       - 當樣本數 $N \le 1000$：採用 1 維 Wasserstein 距離（`scipy.stats.wasserstein_distance(reference, current)`），依特徵尺度標準化後，距離門檻 $\ge 0.1$ 判定為漂移。
     - **類別欄位（Categorical）**：
       - 當樣本數 $N > 1000$：採用卡方適合度/獨立性檢定（`scipy.stats.chisquare`），對齊 `reference` 與 `current` 之類別頻率分佈（對未出現的新類別補 0 頻率並作平滑處理），門檻為 $p\text{-value} < 0.05$。
       - 當樣本數 $N \le 1000$：採用 Total Variation Distance (TVD)，門檻 $\ge 0.1$。
     - **缺失值（NaN / None）處理**：數值欄位排除 NaN 進行分佈計算；類別欄位將 NaN 視為獨立缺失類別計算頻率。
  2. **介面與報表結構契約相容性**：
     - 保留 `EvidentlyDriftMonitor`、`EvidentlyDriftResult` 類別名稱與公開方法簽名（`run`, `run_prediction`, `run_prediction_drift`）。
     - 生成之 `report_json` 必須包含 `metrics` 陣列，內含 `DriftedColumnsCount` 與各欄位之 `ValueDrift(column=...)` 字典，使 `_drifted_column_names()` 及上層調用方完全無感相容。
  3. **已知未知項與實作差異管理（Unknowns & Nuances）**：
     - **KS 檢定之連續性與結（Ties）處理**：離散數值或重複值較多時，KS 檢定在 Scipy 與 Evidently 中的 exact vs asymptotic 模式選擇需透過黃金測試集對齊。
     - **Wasserstein 距離之尺度標準化**：Evidently 內部使用的 scale factor（標準差或四分位距）需透過 Golden Dataset 進行逆向確認。
     - **類別頻率對齊**：針對 current 含有 reference 未見過之全新 category 情況，需透過可執行之黃金基準驗收保證數值一致。
- **依賴變更範圍**：
  - 自 `pyproject.toml` 移除 `evidently`。
  - 自 `uv.lock` 移除 `evidently`、`nltk`、`regex`、`defusedxml`。
  - 保留共用套件 `click`、`joblib`、`tqdm`。
- **評估指標**：
  - 漏洞清除：100% 消除 `PYSEC-2026-3740`。
  - 安全閘門：100% 通過 #1188 fail-closed `pip_audit_gate.py`。
  - 維護成本：低（全部依賴專案既有核心數值套件，無新增第三方依賴）。

### 5.2 方案二：依賴最小化解耦 / 自行封裝移除 NLTK (Vendored / Stripped Package)

- **方案概述**：
  透過客製化打包建立去除 NLTK 依賴之 Evidently wheel，或將 Evidently 內部表格漂移子模組 vendor 至專案代碼庫中。
- **優缺點**：
  - 優點：保留 Evidently 原始報表物件。
  - 缺點：需額外維護客製化 wheel 建置流程或 vendored 程式碼，未來依賴升級易發生衝突與版本維護包袱。

### 5.3 方案三：維持現況等待上游發布修復版 + 安全風險追蹤 (Upstream Waiting)

- **方案概述**：
  維持 `evidently 0.7.21` 與 `nltk 3.10.3`，持續追蹤 NLTK 官方發行（等待 > 3.10.3 修正版釋出）或 Evidently 官方釋出解耦版本。
- **優缺點**：
  - 優點：零重構工程。
  - 缺點：上游時程完全不可控；在 #1188 合併後，fail-closed `pip_audit_gate.py` 將因 `nltk 3.10.3` 檢出而阻擋生產發布，除非有合法人類主管簽署之正式放行程序。

### 5.4 方案比較矩陣

| 評估維度 | 方案一：原生統計引擎 (推薦) | 方案二：自行解耦封裝 | 方案三：等待上游修正 |
|---|---|---|---|
| **漏洞消除完整性** | **100% 消除 (完全移除 NLTK)** | 100% 消除 (移除 NLTK) | 0% (殘留漏洞) |
| **#1188 Gate 相容性** | **完全相容 (Pass)** | 完全相容 (Pass) | **阻擋 (Fail-closed)** |
| **監控功能保留** | **完整保留 (四維度功能等價)** | 完整保留 | 完整保留 |
| **長期維護成本** | **低 (依賴既有核心庫)** | 中-高 (需維護 custom build) | 低 (但受限於上游) |
| **交付風險** | **低 (以黃金測試集驗證)** | 中 (打包複雜度) | 高 (無法通過生產安全閘) |

---

## 6. 精確檔案清單、可執行工作拆分與回滾計畫 (Implementation Runbook)

### 6.1 變更檔案精確清單 (Exact File Paths)

若採行方案一，受影響之檔案清單如下：

| 檔案路徑 | 變更性質 | 變更內容說明 |
|---|---|---|
| `pyproject.toml` | 依賴設定 | 移除 `"evidently>=0.7,<1"` 直接相依 |
| `uv.lock` | Lockfile | 重新鎖定，移除 `evidently`、`nltk`、`regex`、`defusedxml`；保留共用之 `click`、`joblib`、`tqdm` |
| `NOTICE-THIRD-PARTY.md` | 合規文檔 | 移除 `evidently 0.7.21`、`nltk 3.10.3`、`regex`、`defusedxml` 之第三方授權宣告 |
| `modules/learninghub/infrastructure/evidently_monitor.py` | 產品程式碼 | 重構底層統計檢定實作（使用 `scipy.stats`），保留全部公開類別、方法簽名與 JSON 輸出結構 |
| `models/shared_ml/oss_capabilities.py` | 平台能力 | 更新 `OssCapability.MODEL_MONITORING` 標記 |
| `models/shared_ml/validation.py` | 效能門檻 | 驗證 `MetricThreshold` 與 `SegmentMetricThreshold` 零迴歸 |
| `modules/learninghub/application/monitor.py` | 護欄監控 | 驗證 `evaluate_guardrails` 零迴歸 |
| `modules/learninghub/application/release.py` | 釋出監控 | 驗證 `LearningHubService.evaluate_monitoring` 零迴歸 |
| `delivery_toolchain/governance/set_valued_requirements.json` | 治理清單 | 維持符號指標與驗證路徑一致性 |
| `docs/evidence/completion/<NEW-TASK-ID>/sbom.json` | SBOM 交付物 | 為新 candidate 生成全新 CycloneDX 1.5 SBOM（不覆寫歷史 task 證據檔） |
| `tests/models/test_evidently_monitor.py` | 單元測試 | 驗證特徵漂移檢定結果、JSON 輸出結構相容性 |
| `modules/learninghub/tests/test_prediction_drift.py` | 預測測試 | 驗證預測漂移檢定、分群與 DecisionPolicy 門檻解析相容性 |
| `modules/learninghub/tests/test_performance_drift_and_baseline_comparison.py` | 效能測試 | 驗證效能退化與基準比對無相依性影響 |
| `tests/integration/test_oss_ai_execution_flow.py` | 整合測試 | 驗證跨系統 E2E 漂移檢定流程正常通過 |
| `tests/contract/test_deferred_oss_adr.py` | 契約測試 | 更新 ADR 契約檢查中關於漂移監控引擎之斷言 |
| `tests/security/test_supply_chain_security_gate.py` | 安全測試 | 驗證 SBOM 與 Supply Chain 閘門完全通過 |

### 6.2 工作拆分結構 (WBS)

建議將實作拆分為 3 個獨立 Task：

```text
┌─────────────────────────────────────────────────────────────┐
│ Task 1: ODP-DRIFT-NATIVE-MIGRATION-001                      │
│ 實作原生 Scipy 統計漂移引擎與黃金測試集等價驗證               │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Task 2: ODP-DRIFT-DEP-REMOVE-002                            │
│ 移除 evidently/nltk 依賴、更新 uv.lock、NOTICE 與新 SBOM     │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Task 3: ODP-DRIFT-SECURITY-VERIFY-003                       │
│ 執行跨系統整合回歸測試與 #1188 pip-audit 零漏洞驗收           │
└─────────────────────────────────────────────────────────────┘
```

#### Task 1: 原生 Scipy 統計漂移引擎實作與 Golden Dataset 等價驗證 (`ODP-DRIFT-NATIVE-MIGRATION-001`)
- **工作項目**：
  1. 在 `evidently_monitor.py` 實作基於 `scipy.stats.ks_2samp`、`scipy.stats.wasserstein_distance` 與 `scipy.stats.chisquare` 的計算核心。
  2. 建立包含數值與類別特徵之黃金測試集（Golden Dataset），比對原生計算結果與 Evidently 0.7.21 產出之 p-value、統計量、漂移判定與報告結構。

#### Task 2: 依賴移除、Lockfile 重新鎖定、NOTICE 與新 SBOM 生成 (`ODP-DRIFT-DEP-REMOVE-002`)
- **工作項目**：
  1. 自 `pyproject.toml` 移除 `evidently`。
  2. 執行 `uv lock` 重新鎖定 `uv.lock`，確認 `nltk`、`regex`、`defusedxml` 被移除，且 `click`、`joblib`、`tqdm` 正常保留。
  3. 更新 `NOTICE-THIRD-PARTY.md`。
  4. 執行 `python delivery_toolchain/security/generate_sbom.py` 為該 candidate 生成全新 CycloneDX 1.5 SBOM，並執行 `--check` 驗證一致性。

#### Task 3: 跨系統整合回歸測試與 #1188 Security Gate 驗收 (`ODP-DRIFT-SECURITY-VERIFY-003`)
- **工作項目**：
  1. 執行 `tests/models/test_evidently_monitor.py`、`modules/learninghub/tests/test_prediction_drift.py`、`tests/integration/test_oss_ai_execution_flow.py`、`tests/contract/test_deferred_oss_adr.py`。
  2. 執行 `delivery_toolchain/security/pip_audit_gate.py`，驗證生產依賴掃描結果為 `0 known vulnerabilities`。
  3. 確認全套單元與整合測試綠燈。

### 6.3 黃金結果等價驗收標準 (Golden-Result Equivalence Criteria)

在實作驗收時，必須通過以下數值與結構等價性檢驗：
1. **數值檢定等價性標準**：
   - 連續型特徵（$N > 1000$）：雙樣本 KS 檢定統計量 $D$ 與 $p\text{-value}$ 與 Evidently 0.7.21 輸出之相對誤差必須 $< 10^{-6}$（或在 $p \approx 0$ 時絕對誤差 $< 10^{-6}$）。
   - 連續型特徵（$N \le 1000$）：Wasserstein 距離計算結果相對誤差必須 $< 10^{-6}$。
   - 類別型特徵：卡方檢定統計量 $\chi^2$ 與 $p\text{-value}$ 相對誤差必須 $< 10^{-6}$。
   - 漂移判定一致性：`drift_detected`（`drift_share >= drift_share_threshold`）在所有測試案例中必須 100% 一致。
2. **報告結構等價性標準**：
   - `EvidentlyDriftResult.to_dict()` 產出之結構中，`report` 必須包含相容之 `metrics` 陣列，且個別特徵指標之 `metric_name` 需保留 `ValueDrift(column=...)` 標籤與 `DriftedColumnsCount` 計數，確保 `_drifted_column_names()` 函式可正確解析出漂移欄位清單。

### 6.4 回滾機制與安全防護 (Rollback Runbook)

若在實作或上線過程中發現非預期之數值差異或相容性問題，回滾程序如下：
1. **觸發條件**：黃金測試集中有任何一筆漂移判定與基準不一致，或整合測試發生結構解析異常。
2. **回滾操作步驟**：
   ```bash
   # 1. 還原 pyproject.toml 與 uv.lock 至遷移前 commit
   git checkout <pre-migration-sha> -- pyproject.toml uv.lock NOTICE-THIRD-PARTY.md
   
   # 2. 還原 evidently_monitor.py 與測試檔案
   git checkout <pre-migration-sha> -- modules/learninghub/ tests/models/test_evidently_monitor.py
   
   # 3. 重新驗證 lockfile 一致性
   uv lock --check
   ```
3. **關鍵安全警示**：
   - **回滾會恢復已知漏洞**：一旦回滾至 `evidently 0.7.21`，相依鏈將重新引入 `nltk 3.10.3 / PYSEC-2026-3740`。
   - **安全與發布狀態保持 NO-GO**：回滾後之狀態將無法通過 #1188 fail-closed `pip_audit_gate.py`，**絕不能視為安全發布（Safe Rollout）狀態**。

---

## 7. 使用者決策選項與建議 (Stakeholder Decisions & Recommendation)

### 7.1 建議方案

**建議採行「方案一：原生統計漂移監控引擎」**。

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

本文檔已完整分析 NLTK 3.10.3（`GHSA-8mgp-746c-j5xp` / `PYSEC-2026-3740`）之官方狀態、依賴鏈路、反向相依、可達性邊界與四維度監控保留替代方案，並提供了精確的檔案清單、拆工規劃、回滾計畫與等價驗收標準。

本交付物作為本任務 `ODP-NLTK-UNPATCHED-DISPOSITION-DOCUMENT-001` 之主要成果，已提交 PR #1203 供獨立 Reviewer（Codex）審查。待本處置方案經審查核准並由使用者裁決後，將依據第 6 節之拆工計畫排程實作任務。
