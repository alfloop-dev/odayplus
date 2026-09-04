---
doc_id: ODP-PYTHON-PRODUCTION-DEPENDENCY-REMEDIATION-001-SECURITY-EVIDENCE
title: Python Production 相依套件漏洞修復與 NLTK PYSEC-2026-3740 曝險分析佐證
version: 1.0.0
status: approved-evidence
owner: Antigravity6
task: ODP-PYTHON-PRODUCTION-DEPENDENCY-REMEDIATION-001
updated_at: 2026-09-04
---

# Python Production 相依套件漏洞修復與 NLTK PYSEC-2026-3740 曝險分析佐證

## 1. 任務範疇與修復概述

本文件記錄 `ODP-PYTHON-PRODUCTION-DEPENDENCY-REMEDIATION-001` 針對 Production Python 相依套件之已知漏洞修復、lockfile 與 CycloneDX 1.5 SBOM 同步狀態，以及無法脫鉤且無修補版之 `nltk 3.10.3`（`PYSEC-2026-3740`）的完整曝險分析與處置裁決。

依據驗收標準與安全規範：
- 不得使用 suppression、ignore、降級 threshold、排除路徑或關閉 pip-audit。
- 不改動 CI timeout 或 audit routing，維持單一 dependency-audit 驗證路徑。
- 嚴格同步 `pyproject.toml`、`uv.lock` 與兩份 CycloneDX SBOM（`ODP-PGAP-SUPPLY-001` 與 `ODP-OSS-LICENSE-GATE-002`）。

---

## 2. 相依套件漏洞修補與升級清單

針對真實 Production 相依鏈之漏洞，執行以下版本升級與 lockfile 重新鎖定：

| 套件名稱 | 修復前版本 | 修復後版本 | 處置方式與對應 Advisory |
|---|---|---|---|
| `cryptography` | `<49.0` | `>=50.0.0` (50.0.0) | `pyproject.toml` 更新限制並升級，修復 CVE-2024-12797 等已知漏洞 |
| `mlflow` | `3.14.0` | `3.16.0` | `pyproject.toml` 升級直接依賴，修復 GHSA-7w7g-w6cr-3p42 |
| `sqlparse` | `0.5.5` | `0.6.0` | `uv.lock` 同步升級至安全版本，修復 PYSEC-2024-199 |
| `gitpython` | `<3.1.44` | `>=3.1.44` | 間接相依隨 mlflow 升級解析為安全版本 |
| `nltk` | `3.10.0` | `3.10.3` | 升級至 PyPI 目前最新版 3.10.3 |

---

## 3. NLTK PYSEC-2026-3740 評估與無法脫鉤實測結論

### 3.1 上游修補狀態
`pip-audit` 掃描結果顯示 `nltk 3.10.3` 帶有 `PYSEC-2026-3740` advisory，且其 `Fix Versions` 為空（無可用修補版本）。經 PyPI 與 NLTK 官方發行紀錄查證，`3.10.3` 為當前最新發行版本，上游尚未釋出修復版本。

### 3.2 Evidently 相依鏈分析與不可脫鉤性
1. `evidently>=0.7,<1` 為 `pyproject.toml` 之正式 Production 直接依賴，於 `modules/learninghub/infrastructure/evidently_monitor.py` 提供模型特徵分佈漂移監控（`DataDriftPreset`）。
2. PyPI 上 `evidently` 最新版本為 `0.7.21`（無 1.x 系列）。
3. 檢視 `evidently 0.7.21` 之 package metadata，其 `requires_dist` 強制宣告 `nltk>=3.6.7` 為必要相依套件。
4. **實測結論**：在維持 Evidently 數據漂移監控能力的前提下，沒有任何相容之 Evidently 版本可脫鉤或移除 NLTK 相依鏈。

---

## 4. 曝險分析（Exposure & Reachability Analysis）

### 4.1 第一方程式碼呼叫檢查
經完整代碼庫掃描（`grep -rn "nltk" modules apps shared models solver pipelines infra .orchestrator delivery_toolchain scripts`）：
- 專案所有第一方程式碼均**無任何直接 `import nltk` 或呼叫其 API**。

### 4.2 漏洞機制與不可達性判定
- **漏洞成因**：`PYSEC-2026-3740` 屬於檔案沙箱繞過漏洞（File sandbox bypass），發生於 NLTK 的 `TransitionParser.train` 等模型成品（model-artifact）處理 API 使用原始檔案存取 API 處理外部路徑時。
- **資料流與調用邊界**：本專案僅透過 `modules/learninghub/infrastructure/evidently_monitor.py` 調用 `evidently.Report` 及 `evidently.presets.DataDriftPreset` 進行數值與類別特徵漂移計算，完全不調用 Evidently 的 NLP/Text 模組，亦完全未引用 NLTK 之語法剖析器（`TransitionParser`）或模型訓練流程。
- **可達性結論**：外部未經授權請求或輸入無法觸及受影響的 NLTK 程式碼路徑，該漏洞在生產環境下處於**不可達（Unreachable / Non-exploitable）**狀態。

---

## 5. 真實 pip-audit 掃描前後輸出記錄

### 5.1 升級後真實掃描輸出
執行真實 `uv run --with pip-audit pip-audit` 掃描結果如下：

```text
Found 1 known vulnerability in 1 package
Name Version ID              Fix Versions
---- ------- --------------- ------------
nltk 3.10.3  PYSEC-2026-3740 
```

所有具有可用修補版本之 Production 相依套件（`cryptography`, `gitpython`, `mlflow`, `sqlparse` 等）已全數升級至安全版本，無任何殘留之可修補漏洞。

---

## 6. CycloneDX 1.5 SBOM 與 Lockfile 驗證

在包含完整 `node_modules` 與 Python `.venv` 之環境下重新產出兩份 CycloneDX 1.5 SBOM：
1. `docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json`
2. `docs/evidence/completion/ODP-OSS-LICENSE-GATE-002/sbom.json`

經 `delivery_toolchain/security/generate_sbom.py --check` 與 `tests/security/test_supply_chain_security_gate.py` 驗證：
- 共計收錄 798 個相依組件（346 個 npm 組件完整保留 supplier/author 資訊）。
- `components`、`dependencies` 與 `properties` 完全與當前 lockfiles（`package-lock.json`, `uv.lock`）一致。
- 通過 SBOM 一致性防退化測試（`test_sbom_and_provenance_present_and_valid`）。

---

## 7. 後續追蹤與維護處置計畫

1. **上游追蹤**：將 NLTK 與 Evidently 之版本釋出納入例行相依套件監控；一旦 NLTK 釋出修復版本或 Evidently 提供解耦更新，立即排程升級。
2. **合規記錄**：本文件作為 PR #1194 及後續審查之正式中文安全佐證，不採取任何 suppression 設定，維持審計真實透明度。
