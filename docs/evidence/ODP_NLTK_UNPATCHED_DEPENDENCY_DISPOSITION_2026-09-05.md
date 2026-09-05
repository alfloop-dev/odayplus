---
evidence_id: ODP-NLTK-UNPATCHED-DISPOSITION-DOCUMENT-001
title: "NLTK 3.10.3 未修補依賴處置與完整監控保留方案分析"
date: 2026-09-05
status: IMPLEMENTATION_PROPOSAL
owner: Codex2
reviewer: Codex
repository: alfloop-dev/odayplus
task: ODP-NLTK-UNPATCHED-DISPOSITION-DOCUMENT-001
observed_ref: 6c4a8be856a5
base_ref: 6c4a8be856a5
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

由於前次 assessment 任務受限於 non-mutating 規則與任務範疇分類限制，未能產出可供獨立審查的處置與替代方案文檔。本任務 `ODP-NLTK-UNPATCHED-DISPOSITION-DOCUMENT-001` 旨在**正式補齊完整的技術處置分析文件**，提供嚴謹之漏洞訊號矛盾剖析、完整監控能力保留的驗收方案、Pinned Evidently 0.7.21 演算法對齊細節、可執行拆工規劃與黃金測試集驗收標準，供架構團隊與使用者決策。

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
   curl -s https://pypi.org/pypi/nltk/json | jq -r '.info.version'
   ```
   *2026-09-05 讀回紀錄（本次 assessment 的 immutable receipt；僅採信 `.info.version`）*：
   ```text
   3.10.3
   ```
   證明目前 PyPI API 的 `.info.version` 為 `3.10.3`；PyPI 專案頁的 release history 同樣只顯示最高正式版 `3.10.3`。在官方 advisory 尚未提供 patched version 前，本文件將 `3.10.3` 視為受影響。

2. **OSV API 讀回驗證 (`PYSEC-2026-3740`)**：
   ```bash
   curl -s https://api.osv.dev/v1/vulns/PYSEC-2026-3740 | jq '{id: .id, affected: .affected[0].ranges[0].events}'
   ```
   *2026-09-05 讀回紀錄（本次 assessment 的 immutable receipt）*：
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

4. **CI/scanner receipt（僅記錄差異，不作安全證據）**：
   ```bash
   pip-audit -r <(echo "nltk==3.10.3") --no-deps -f json
   ```
   *先前 assessment 留存的 2026-09-05 receipt*：
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
   - **矛盾根源（不作臆測）**：目前可證實的是兩個資料來源對 fixed 邊界不一致；沒有足夠證據把原因歸因於同步時序、commit 或 tag。處置上以 NLTK 官方 advisory（受影響 `<=3.10.3`、patched `None`）及實際發布的 3.10.3 artifact 為較保守的安全基準，並保留 OSV/PyPA 原始 fixed 欄位供追溯。
   - **禁止將 OSV Fixed 或單一 CI scanner 結果視為安全證據**：工具可能依其資料快照將 `3.10.3` 標成 fixed，也可能依 GHSA/CI policy 報警；兩者都不能推翻 NLTK 官方 advisory。只有 NLTK 發布 patched version 且官方 advisory 更新，或經授權的移除/替代實作完成並通過 gate，才可改變 NO-GO 判定。

2. **本地未安裝虛擬環境之 `pip-audit --local` 空輸出**：
   - 執行 `pip-audit --local` 僅會掃描目前啟用的 Python 環境。若該環境未安裝生產鎖定依賴，將輸出 `No known vulnerabilities found`。
   - 此輸出僅代表「本地環境未安裝該套件」，絕非相依鏈安全的證據。唯有針對生產鎖定清單（如 `uv.lock`）進行掃描所得之 `PYSEC-2026-3740` 檢出，才是真實的曝險基準。

---

## 3. `dev` 基底與 #1188 相依鏈、反向相依及可達性深度分析

### 3.1 基準版本與 Exact Lock 相依鏈

- **觀察基準 Ref**：目前 `origin/dev`/本 task composed base `6c4a8be856a5`；另保留 #1188 source-doc-cache snapshot `04e1572f802a54c2646ba678fe2975226dfbd7c4` 作為同一 exact lock lineage 的歷史 receipt。兩者均解析 `evidently==0.7.21 -> nltk==3.10.3`，不可因 branch/receipt 差異而視為安全。
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
   - 可達性邊界：lock resolver 明確把 `evidently==0.7.21` 的 `requires_dist: nltk` 安裝進同一 production environment；這已足以納入 SBOM/漏洞稽核。第一方目前實際呼叫的是 `Report([DataDriftPreset(...)])`（`run` 與 `run_prediction`），來源檔沒有第一方呼叫 NLTK model-artifact API 的證據。這只能界定目前呼叫面，不能宣稱 NLTK API 不可達或免稽核。
   - NLTK 受影響公開 API（逐一列入驗收範圍）：`TransitionParser.train`、`TransitionParser.parse`、`AveragedPerceptron.save`、`AveragedPerceptron.load`、`PerceptronTagger.save_to_json`、`save_maxent_params`；官方 advisory 明確描述這些 model-artifact 讀寫路徑。是否由 Evidently 內部任一 descriptor 實際觸發，必須以 pinned 0.7.21 的可執行 probe/trace 驗證，不以未直接 import 推論。

3. **不可免除稽核原則（Non-Exemption Principle）**：
   - 儘管執行時期未直接呼叫受影響的 parser，但 `nltk 3.10.3` 已隨映像檔打包進生產執行環境與 `sys.path`。
   - 依據專案安全基準與 #1188（`ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001`）fail-closed 規範，**「未直接 import」絕不能作為宣稱完全不可達或免除安全稽核的理由**。

---

## 4. 四維度完整模型監控功能盤點矩陣 (Capabilities Matrix)

為確保替代方案能完整保留所有現有監控功能，以下完整列出平台現有之四維度監控能力矩陣與真實代碼 Owner：

| 監控維度 | 負責模組與進入點 API | 底層演算法與邏輯 (Pinned Evidently 0.7.21 語意) | 資料流與輸入/輸出契約 | 依賴脫鉤影響評估 |
|---|---|---|---|---|
| **1. 資料漂移 (Data Drift)** | `modules/learninghub/infrastructure/evidently_monitor.py`<br>`EvidentlyDriftMonitor.run(...)` | 目前已確認的語意只有：呼叫 pinned `Report([DataDriftPreset(...)])`、傳入 `drift_share_threshold`，並解析 evaluation。KS/Wasserstein/卡方/TVD 的實際選擇、樣本分流、缺失值與門檻不能由本 wrapper 推定；須從 0.7.21 source/runtime receipt 建立 baseline。 | **輸入**：`reference_rows: Sequence[Mapping]`, `current_rows: Sequence[Mapping]`, `drift_share_threshold: float = 0.5`<br>**輸出**：`EvidentlyDriftResult`（含 `drift_detected`、`drifted_columns`、`drift_share`、`drifted_column_names`、`report_json`） | **需重構底層**：先凍結 pinned 0.7.21 golden baseline，再以原生實作逐 case 對齊；未通過前不得宣稱等價 |
| **2. 特徵漂移 (Feature Drift)** | `modules/learninghub/infrastructure/evidently_monitor.py`<br>`_drifted_column_names(...)`<br>`_drift_metric_detected(...)` | • 解析報表 payload 中每個 `ValueDrift(column=..., method=..., threshold=...)` 欄位指標<br>• 依據檢定方法（$p$-value 檢定小於門檻，或距離檢定大於等於門檻）判定個別特徵漂移<br>• 輸出漂移特徵名稱列表 `drifted_column_names` | **輸入**：計算報表 payload 字典<br>**輸出**：`drifted_column_names: tuple[str, ...]` | **需重構底層**：保持既有 JSON `metrics` 陣列格式與 `ValueDrift` 命名相容性 |
| **3. 預測漂移 (Prediction Drift)** | `modules/learninghub/infrastructure/evidently_monitor.py`<br>`EvidentlyDriftMonitor.run_prediction(...)`<br>`EvidentlyDriftMonitor.run_prediction_drift(...)` | 第一方已確認：只選取 caller 指定的 prediction columns，驗證 model/version/cohort/snapshot、output type 與 policy；最後仍以 `DataDriftPreset` 評估 output-only frame。底層統計算法與閾值仍須由 pinned runtime baseline 證實。 | **輸入**：`reference_rows`, `current_rows`, `model_name`, `model_version`, `cohort_key`, `prediction_columns`, `reference_snapshot_id`, `current_snapshot_id`, `policy`<br>**輸出**：`EvidentlyDriftResult`（攜帶 model metadata、cohort、policy version、output types） | **需重構底層**：保留所有第一方驗證與 policy 行為；統計替換須逐案例比對，不得以 wrapper 行為宣稱無縫等價 |
| **4. 效能監控 (Performance Drift)** | **真實 Owner 與 API**：<br>1. 門檻定義與評估：[`models/shared_ml/validation.py`](../../models/shared_ml/validation.py) (`MetricThreshold`, `SegmentMetricThreshold`)<br>2. 護欄評估：[`modules/learninghub/application/monitor.py`](../../modules/learninghub/application/monitor.py) (`evaluate_guardrails`, `ReleaseMonitorAssessment`)<br>3. 服務評估與重訓觸發：[`modules/learninghub/application/release.py`](../../modules/learninghub/application/release.py) (`LearningHubService.evaluate_monitoring`)<br>4. 領域模型：[`modules/learninghub/domain/monitoring.py`](../../modules/learninghub/domain/monitoring.py) (`MonitoringEvaluation`, `MonitoringBreach`, `RetrainingRequest`) | • 模型效能指標衰退評估（AUC, SMAPE, Precision, Recall, Coverage）<br>• 支援絕對門檻（`min_value`, `max_value`）與相對於基準快照之衰退率（`max_degradation`, `max_relative_degradation`）<br>• 依 `DecisionPolicy`（`policy_kind="model_performance_drift"`）觸發重新訓練請求（`RetrainingRequest`）或 Rollback 建議 | **輸入**：`DatasetSnapshot`, `ModelVersion`, `DecisionPolicy`, `observed_metrics`, `baseline_metrics`<br>**輸出**：`ReleaseMonitorAssessment`, `GuardrailBreach`, `RetrainingRequest` | source inspection 顯示這些 owner/API 是第一方實作且未在列示檔案中呼叫 Evidently/NLTK；這是範圍證據，不是 runtime 零迴歸證明。後續 candidate 必須執行效能 regression，才能宣稱保留 |

---

## 5. 依賴處置與架構替代方案深度比較 (Decision Options)

### 5.1 方案一（推薦）：原生統計漂移監控引擎 (Native Scipy/Statsmodels-backed Drift Monitor)

- **方案概述**：
  利用專案既有之頂級數值與統計依賴（`scipy>=1.14`、`numpy>=2.0`、`pandas>=2.2`、`statsmodels>=0.14`），在 `modules/learninghub/infrastructure/evidently_monitor.py` 實作輕量原生統計漂移引擎，徹底替換 `evidently`。
- **Pinned Evidently 0.7.21 演算法實作與等價語意細節**：
  1. coordination precheck 的 pinned package source inspection receipt（`evidently/.../registry.py:137-159`，版本 `0.7.21`）記錄：`N <= 1000` 且 numeric unique > 5 使用 KS，否則使用 chi/z；`N > 1000` 且 numeric unique > 5 使用 Wasserstein，否則使用 Jensen–Shannon；Wasserstein scale 為 `max(std(reference), 0.001)`。這是算法選擇/scale 的 source receipt，不等於全功能等價已驗證；threshold、categorical/NaN 行為及 report serialization 仍必須由 runtime baseline 讀回。
  2. 後續實作者必須在隔離環境 pin `evidently==0.7.21`，用固定 reference/current fixtures 執行實際 `DataDriftPreset`，保存每欄位 metric、統計量、p-value/距離、threshold、drift share、缺失值與 dtype 結果；再以 source inspection/trace 解釋每個結果。`N=200`、`20MB` 與 `100% clean` 只是待驗證的測試條件/門檻，不是本文件已證實的算法或安全結果。
  2. **介面與報表結構契約相容性**：
     - 保留 `EvidentlyDriftMonitor`、`EvidentlyDriftResult` 類別名稱與公開方法簽名（`run`, `run_prediction`, `run_prediction_drift`）。
     - 生成之 `report_json` 必須包含 `metrics` 陣列，內含 `DriftedColumnsCount` 與各欄位之 `ValueDrift(column=...)` 字典，使 `_drifted_column_names()` 及上層調用方完全無感相容。
  3. **已知未知項與實作差異管理（Unknowns & Nuances）**：
     - 已知 source receipt 不涵蓋 thresholds、categorical 分支、缺失值、ties、zero-variance、全新 category 與 report serialization；這些項目在 baseline 未產出前維持 `UNKNOWN`。
     - 替代引擎不得把上述算法選擇改寫成 KS/Wasserstein/卡方/TVD 的簡化假設；必須逐 case 對齊實際 0.7.21 receipt。
- **依賴變更範圍**：
  - 自 `pyproject.toml` 移除 `evidently`。
  - 自 `uv.lock` 移除 `evidently`、`nltk`、`regex`、`defusedxml`。
  - 保留共用套件 `click`、`joblib`、`tqdm`。
- **評估指標**：
  - 漏洞清除（candidate 驗收目標，非本 task 已證實結果）：完成後必須以 SBOM 與 fail-closed gate receipt 證明已消除 `PYSEC-2026-3740`。
  - 安全閘門（candidate 驗收目標，非本 task 已證實結果）：完成後必須通過 #1188 `pip_audit_gate.py`；本 assessment 不預宣稱通過。
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
| **漏洞消除完整性** | 目標為移除 NLTK；須以 candidate SBOM/gate 證明 | 目標為移除 NLTK；須以 candidate SBOM/gate 證明 | 0%（漏洞保留，NO-GO） |
| **#1188 Gate 相容性** | 待實作後驗證 | 待實作後驗證 | 阻擋（Fail-closed） |
| **監控功能保留** | 待 golden baseline 驗證四維度契約 | 待 golden baseline 驗證 | 現有功能保留但不解除安全風險 |
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
| `docs/evidence/completion/<NEW-TASK-ID>/sbom.json` | SBOM 交付物 | 為新 candidate 生成 CycloneDX 1.5 SBOM；執行前必須先處理 `delivery_toolchain/security/generate_sbom.py:664-666` 對歷史 `EVIDENCE_TASK_DIR` 的 mirror 副作用，禁止覆寫其他 task receipt |
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
  4. 先由獲授權的 security-tooling task 收斂 `generate_sbom.py:664-666` 的歷史 evidence mirror 副作用；再以 candidate 專屬輸出生成 CycloneDX 1.5 SBOM 並執行 `--check`。本文件 task 不直接執行該工具，也不新增第二套 SBOM。

#### Task 3: 跨系統整合回歸測試與 #1188 Security Gate 驗收 (`ODP-DRIFT-SECURITY-VERIFY-003`)
- **工作項目**：
  1. 執行 `tests/models/test_evidently_monitor.py`、`modules/learninghub/tests/test_prediction_drift.py`、`tests/integration/test_oss_ai_execution_flow.py`、`tests/contract/test_deferred_oss_adr.py`。
  2. 執行 `delivery_toolchain/security/pip_audit_gate.py`，驗證生產依賴掃描結果為 `0 known vulnerabilities`。
  3. 確認全套單元與整合測試綠燈。

### 6.3 黃金結果等價驗收標準 (Golden-Result Equivalence Criteria)

在實作驗收時，必須通過以下數值與結構等價性檢驗：
1. **數值檢定等價性標準**：
   - 先以 pinned 0.7.21 對 numeric/categorical、缺失值、常數欄位、新類別、不同樣本量建立 immutable golden JSON；不得預設 KS、Wasserstein、卡方或 TVD，實際方法/閾值必須從 receipt 讀回。
   - 替代引擎逐欄比對統計量、p-value/距離、threshold、drift flag、share 與欄位名稱；容差須按實際算法決定並由 reviewer 核准，不能先宣稱 `<10^-6`。
   - 漂移判定一致性：`drift_detected`（`drift_share >= drift_share_threshold`）在所有測試案例中必須 100% 一致。
2. **報告結構等價性標準**：
   - `EvidentlyDriftResult.to_dict()` 產出之結構中，`report` 必須包含相容之 `metrics` 陣列，且個別特徵指標之 `metric_name` 需保留 `ValueDrift(column=...)` 標籤與 `DriftedColumnsCount` 計數，確保 `_drifted_column_names()` 函式可正確解析出漂移欄位清單。

3. **可執行 golden baseline 與未知項**：
   - baseline runner 固定 `evidently==0.7.21`、Python/OS、input fixture hash、reference/current row counts 與 dtype；保存 raw `evaluation.json`、normalized contract JSON、套件 lock/SBOM receipt。
   - fixture 至少涵蓋 200 rows、>1000 rows、缺失值、常數值、類別增減、numeric/categorical prediction output、cohort mismatch 與 policy threshold；20MB/100% clean 是另行的資源與安全 gate 驗收，不是算法等價證明。
   - 未知項（算法選擇、分流、尺度、平滑、NaN、報表欄位命名）在 baseline 未產出前一律標記 `UNKNOWN`，不得以文件推定為已解決。

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
1. **安全性方向最優**：目標是徹底移除 NLTK 未修補漏洞；是否達成須由 candidate SBOM、實際 lock 與 gate receipt 證明，不能由本 assessment 預宣稱。
2. **合規性方向最優**：保留 #1188 fail-closed security gate，不新增 waiver/suppression；是否通過須待後續實作驗收。
3. **系統影響最小**：監控數學邏輯完全透明可控，零外部網路與肥大相依負擔。

### 7.2 需使用者 / 決策團隊確認之事項

請架構主管與決策團隊確認以下項目：
- [ ] **確認採行方案一**：授權開立後續實作任務（`ODP-DRIFT-NATIVE-MIGRATION-001` 等）進行程式碼與相依性重構。
- [ ] **確認介面相容性策略**：同意保留 `EvidentlyDriftMonitor` 與 `EvidentlyDriftResult` 作為相容名稱，以確保上層調用方完全無感。

---

## 8. 結論與後續交付

本文檔已完整分析 NLTK 3.10.3（`GHSA-8mgp-746c-j5xp` / `PYSEC-2026-3740`）之官方狀態、依賴鏈路、反向相依、可達性邊界與四維度監控保留替代方案，並提供了精確的檔案清單、拆工規劃、回滾計畫與等價驗收標準。

本交付物是本任務 `ODP-NLTK-UNPATCHED-DISPOSITION-DOCUMENT-001` 的唯一 task-owned 產物；它只交付可審查的處置方案，不宣稱漏洞已修復、監控已完成或 gate 已通過。後續須先取得使用者對架構選項的決策，再依第 6 節拆成實作、依賴、SBOM/NOTICE 與 security-gate 任務；本 task 以文件 PR 的獨立 review 為交付終點。
