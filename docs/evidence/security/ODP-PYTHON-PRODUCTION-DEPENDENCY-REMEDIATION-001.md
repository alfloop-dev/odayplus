---
doc_id: ODP-PYTHON-PRODUCTION-DEPENDENCY-REMEDIATION-001-SECURITY-EVIDENCE
title: Python Production 相依套件漏洞修復與 NLTK PYSEC-2026-3740 曝險分析佐證
version: 1.0.0
status: approved-evidence
owner: Codex
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
| `cryptography` | `48.0.1` | `50.0.1` | `pyproject.toml` 約束為 `>=50.0.1,<51`；實測清除 `PYSEC-2026-3552`、`PYSEC-2026-3553`、`PYSEC-2026-3554`（分別需 `>=50.0.0`、`>=49.0.0`、`>=49.0.0`） |
| `mlflow` | `3.14.0` | `3.16.0` | 直接依賴升級；實測清除 `PYSEC-2026-3687`、`GHSA-gqvg-gmmx-x4hm`、`CVE-2026-71211`（均需 `>=3.15.0`） |
| `sqlparse` | `0.5.5` | `0.6.0` | `uv.lock` 同步升級；實測清除 `PYSEC-2026-3696`、`PYSEC-2026-3697`、`PYSEC-2026-3698`、`PYSEC-2026-3699`、`CVE-2026-84305`（均需 `>=0.6.0`） |
| `gitpython` | `3.1.58` | `3.1.61` | 間接相依隨 MLflow 升級解析；實測清除 `PYSEC-2026-3785`、`PYSEC-2026-3786`、`PYSEC-2026-3787`、`PYSEC-2026-3788`（均需 `>=3.1.59`） |
| `nltk` | `3.10.0` | `3.10.3` | 升級至 PyPI 目前最新版；實測清除 22 筆舊版 advisory，但 `PYSEC-2026-3740` 仍無修補版本，詳見第 3 節 |

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

## 5. 真實 pip-audit 升級前後輸出記錄

### 5.1 升級前真實掃描輸出

以 lockfile 升級前的五個 production 版本 pinned 掃描（`--no-deps`，不依賴
`--local` 是否指向正確的虛擬環境）：

```text
$ pip-audit -r <base 版本> --no-deps -f json
Found 40 known vulnerabilities in 5 packages
```

這次掃描實際回報並由升級清除的 advisory 為：`cryptography` 的
`PYSEC-2026-3552`、`PYSEC-2026-3553`、`PYSEC-2026-3554`；`gitpython` 的
`PYSEC-2026-3785`、`PYSEC-2026-3786`、`PYSEC-2026-3787`、`PYSEC-2026-3788`；
`mlflow` 的 `PYSEC-2026-3687`、`GHSA-gqvg-gmmx-x4hm`、
`CVE-2026-71211`；以及 `sqlparse` 的 `PYSEC-2026-3696`、
`PYSEC-2026-3697`、`PYSEC-2026-3698`、`PYSEC-2026-3699`、
`CVE-2026-84305`。舊版 `nltk` 另有多筆 findings，升級到 3.10.3 後僅剩
`PYSEC-2026-3740`。

### 5.2 升級後真實掃描輸出

以升級後的五個 pinned 版本執行真實 `pip-audit -r ... --no-deps`，結果如下：

```text
Found 1 known vulnerability in 1 package
Name  Version  ID                Fix Versions
----  -------  ----------------  -----------
nltk  3.10.3   PYSEC-2026-3740   (none)
```

所有具有可用修補版本之 production 相依套件（`cryptography`、`gitpython`、
`mlflow`、`sqlparse`）已全數升級，唯一殘留是上游尚未提供修補版的
`nltk 3.10.3 / PYSEC-2026-3740`。在目前 worktree 的 `pip-audit --local`
路徑則會得到 `No known vulnerabilities found`，因該路徑稽核的是空的/未對應的
local 環境；此輸出不能取代上述針對實際 pinned production 版本的掃描。

---

## 6. CycloneDX 1.5 SBOM 與 Lockfile 驗證

在包含完整 `node_modules` 與 Python `.venv` 之環境下重新產出兩份 CycloneDX 1.5 SBOM：
1. `docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json`
2. `docs/evidence/completion/ODP-OSS-LICENSE-GATE-002/sbom.json`

經 `delivery_toolchain/security/generate_sbom.py --check` 與 `tests/security/test_supply_chain_security_gate.py` 驗證：
- 共計收錄 798 個相依組件：554 個 npm 與 244 個 PyPI 組件；其中 346 個 npm 組件帶有 `supplier`。SBOM 沒有宣稱保留 `author` 欄位，因目前 component 的 `author` 欄位數量為 0。
- `components`、`dependencies` 與 `properties` 完全與當前 lockfiles（`package-lock.json`, `uv.lock`）一致。
- 通過 SBOM 一致性防退化測試（`test_sbom_and_provenance_present_and_valid`）。

---

## 7. 後續追蹤、#1188 交互作用與維護處置計畫

1. **現況與限制**：runtime `Evidently 0.7.21` 仍需要 `nltk>=3.6.7`，因此 runtime 依賴鏈仍會安裝 `nltk 3.10.3`。`PYSEC-2026-3740` 沒有任何 `fix_versions`；本 task 不能把它記成已修復，也不能為了讓 gate 變綠而 suppress、ignore、移除 Evidently/runtime 依賴或假稱 clean。
2. **#1188 fail-closed 交互作用**：目前 `Makefile` 的 `pip-audit --local` 會因 local 環境未對應 production 安裝而回報空稽核；這不是殘留 NLTK 漏洞已消失的證據。既有 #1188（`ODP-CI-DEPENDENCY-AUDIT-BOUNDARY-001`）合併後的 `pip_audit_gate.py` 會掃描實際 `.venv` 中的套件，且任何一筆 pip-audit finding 都以 exit 1 fail-closed。因此 #1188 一旦採用該路徑，`nltk 3.10.3 / PYSEC-2026-3740` 會使 gate 失敗；本 task 不改 audit routing，並將此列為明確的 follow-up blocker。
3. **後續追蹤與驗收**：另開 follow-up 時，必須有明確的 Evidently/NLTK 遷移設計、替代監控能力、依賴移除或上游修補的驗收條件，以及 #1188 gate 的協調方案；未完成前不可宣稱 production dependency audit clean。持續追蹤 NLTK 與 Evidently 發行；一旦 NLTK 釋出修復版本或 Evidently 提供解耦更新，立即排程升級並重跑完整 audit。
4. **本 task 的可稽核交付邊界**：本 task 完成可修補 advisory 的 dependency/lockfile 修復、SBOM 正確性與上述 residual-risk 揭露；不以任何方式偽裝無修補漏洞已被消除。此文件作為 PR #1194 及後續審查之正式中文安全佐證。

---

## 8. 本次 owner dispatch 驗證紀錄

- `uv lock --check`：通過。
- `uv run --frozen python delivery_toolchain/security/generate_sbom.py --check`：通過；兩份 SBOM 的 components/dependencies/properties 與 lockfiles 一致。
- focused security、OSS license/notice、Evidently/OSS flow 與 smoke regression：全部通過；smoke 為 `3 passed`。
- `make ci`：本次只執行一次。ruff、npm audit（`found 0 vulnerabilities`）與 Python `pip-audit --local`（`No known vulnerabilities found`）通過；完整 `tests/security` 已實際跑到 `260 passed`，其後在測試內再次呼叫 npm audit 時因 registry endpoint 長時間無回應而中止。這不是 Python dependency assertion 失敗，也不代表已完成全綠 full CI；PR 需由遠端 CI 在 npm endpoint 可用時提供最終結果。

## 9. PR CI 失敗分流與修復邊界

- PR #1194 的完整 CI run `33860234193` 已完成 Python product 測試，結果為 `4871 passed, 1 failed, 23 skipped`；唯一失敗是 `tests/security/test_supply_chain_security_gate.py::test_npm_audit_passes`。失敗輸出為 npm registry `POST https://registry.npmjs.org/-/npm/v1/security/audits/quick` 回覆 `503 Service Unavailable`；同一 run 的 `npm ci` 安裝稽核仍為 `found 0 vulnerabilities`。因此這不是本 task 的 Python dependency、lockfile 或 SBOM assertion 失敗。
- 隨後 head `a8385cf` 的 run `33865579581` 在 review gate 重新開啟後被取消：product 尚未進入 security step，E2E 也在 `npm ci` 期間取消。該 run 不視為成功的 full CI，也不被本文件用來宣稱驗收通過。
- 修復邊界維持不變：本 task 不修改 `.github/workflows/ci.yml`、`Makefile`、npm manifest/lockfile 或 audit routing；npm registry/共用 npm audit gate 的修復由相應的共用 gate task 處理。本 task 僅重新提交已完成的 Python dependency、lockfile、SBOM 與中文 residual-risk evidence，等待遠端 CI 在服務可用時提供新 run 的結果。
